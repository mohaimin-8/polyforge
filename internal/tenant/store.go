package tenant

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"sort"
	"strings"
	"sync"
	"time"
)

var (
	ErrTenantNotFound  = errors.New("tenant not found")
	ErrProjectNotFound = errors.New("project not found")
	ErrAPIKeyNotFound  = errors.New("api key not found")
	ErrUnauthorized    = errors.New("unauthorized")
	ErrForbidden       = errors.New("forbidden")
	ErrConflict        = errors.New("resource already exists")
)

const (
	DefaultPageSize = 50
	MaxPageSize     = 200
)

// API-key scopes. A read key can call read-only endpoints; a full key can
// also mutate. Scope is fixed at key creation and survives rotation, so
// widening access always requires issuing a new credential.
const (
	ScopeRead = "read"
	ScopeFull = "full"
)

// NormalizeScope maps the empty scope to full so credentials issued before
// scopes existed keep working unchanged.
func NormalizeScope(scope string) string {
	scope = strings.TrimSpace(scope)
	if scope == "" {
		return ScopeFull
	}
	return scope
}

func ValidScope(scope string) bool {
	switch NormalizeScope(scope) {
	case ScopeRead, ScopeFull:
		return true
	default:
		return false
	}
}

// ScopeAllows reports whether a key with keyScope may perform an action that
// requires requiredScope.
func ScopeAllows(keyScope, requiredScope string) bool {
	keyScope = NormalizeScope(keyScope)
	switch requiredScope {
	case ScopeRead:
		return keyScope == ScopeRead || keyScope == ScopeFull
	case ScopeFull:
		return keyScope == ScopeFull
	default:
		return false
	}
}

type Tenant struct {
	ID            string    `json:"id"`
	Name          string    `json:"name"`
	Plan          string    `json:"plan"`
	IsolationMode string    `json:"isolation_mode"`
	CreatedAt     time.Time `json:"created_at"`
}

type Project struct {
	ID        string    `json:"id"`
	TenantID  string    `json:"tenant_id"`
	Name      string    `json:"name"`
	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
}

type ProjectPage struct {
	Items      []Project `json:"items"`
	NextCursor string    `json:"next_cursor,omitempty"`
}

type PageRequest struct {
	Limit  int
	Cursor string
}

type APIKey struct {
	ID        string     `json:"id"`
	TenantID  string     `json:"tenant_id"`
	Name      string     `json:"name"`
	Scope     string     `json:"scope"`
	Prefix    string     `json:"prefix"`
	Secret    string     `json:"secret,omitempty"`
	CreatedAt time.Time  `json:"created_at"`
	RevokedAt *time.Time `json:"revoked_at,omitempty"`
}

type Repository interface {
	ProvisionTenant(ctx context.Context, t Tenant, keyName string) (Tenant, APIKey, error)
	CreateTenant(ctx context.Context, t Tenant) (Tenant, error)
	ListTenants(ctx context.Context) ([]Tenant, error)
	Tenant(ctx context.Context, id string) (Tenant, bool, error)
	PromoteTenantIsolation(ctx context.Context, tenantID, mode string) (Tenant, error)
	CreateProject(ctx context.Context, p Project) (Project, error)
	ListProjects(ctx context.Context, tenantID string, page PageRequest) (ProjectPage, error)
	Project(ctx context.Context, tenantID, projectID string) (Project, error)
	UpdateProject(ctx context.Context, p Project) (Project, error)
	DeleteProject(ctx context.Context, tenantID, projectID string) error
	CreateAPIKey(ctx context.Context, tenantID, name, scope string) (APIKey, error)
	ListAPIKeys(ctx context.Context, tenantID string) ([]APIKey, error)
	RevokeAPIKey(ctx context.Context, tenantID, keyID string) error
	RotateAPIKey(ctx context.Context, tenantID, keyID, name string) (APIKey, error)
	AuthenticateAPIKey(ctx context.Context, tenantID, secret string) (APIKey, error)
}

type Store struct {
	mu        sync.RWMutex
	tenants   map[string]Tenant
	projects  map[string]Project
	apiKeys   map[string]apiKeyRecord
	outbox    []outboxRow
	outboxIDs map[string]struct{}
}

type apiKeyRecord struct {
	key  APIKey
	hash string
}

func NewStore() *Store {
	return &Store{
		tenants:   map[string]Tenant{},
		projects:  map[string]Project{},
		apiKeys:   map[string]apiKeyRecord{},
		outboxIDs: map[string]struct{}{},
	}
}

func (s *Store) ProvisionTenant(_ context.Context, t Tenant, keyName string) (Tenant, APIKey, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	t = NormalizeTenant(t)
	if _, exists := s.tenants[t.ID]; exists {
		return Tenant{}, APIKey{}, ErrConflict
	}
	key, hash, err := NewRandomAPIKey(t.ID, keyName, ScopeFull)
	if err != nil {
		return Tenant{}, APIKey{}, err
	}
	event, err := AuditTenantProvisioned(t, key)
	if err != nil {
		return Tenant{}, APIKey{}, err
	}
	s.tenants[t.ID] = t
	s.apiKeys[key.ID] = apiKeyRecord{key: KeyWithoutSecret(key), hash: hash}
	s.appendOutboxLocked(event)
	return t, key, nil
}

func (s *Store) CreateTenant(_ context.Context, t Tenant) (Tenant, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	t = NormalizeTenant(t)
	if _, exists := s.tenants[t.ID]; exists {
		return Tenant{}, ErrConflict
	}
	event, err := AuditTenantCreated(t)
	if err != nil {
		return Tenant{}, err
	}
	s.tenants[t.ID] = t
	s.appendOutboxLocked(event)
	return t, nil
}

func (s *Store) ListTenants(_ context.Context) ([]Tenant, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	out := make([]Tenant, 0, len(s.tenants))
	for _, t := range s.tenants {
		out = append(out, t)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	return out, nil
}

func (s *Store) Tenant(_ context.Context, id string) (Tenant, bool, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	t, ok := s.tenants[id]
	return t, ok, nil
}

func (s *Store) CreateProject(_ context.Context, p Project) (Project, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	if _, ok := s.tenants[p.TenantID]; !ok {
		return Project{}, ErrTenantNotFound
	}
	p = NormalizeProject(p)
	key := projectKey(p.TenantID, p.ID)
	if _, exists := s.projects[key]; exists {
		return Project{}, ErrConflict
	}
	event, err := AuditProjectCreated(p)
	if err != nil {
		return Project{}, err
	}
	s.projects[key] = p
	s.appendOutboxLocked(event)
	return p, nil
}

func (s *Store) ListProjects(_ context.Context, tenantID string, page PageRequest) (ProjectPage, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	if _, ok := s.tenants[tenantID]; !ok {
		return ProjectPage{}, ErrTenantNotFound
	}
	page = NormalizePageRequest(page)
	projects := make([]Project, 0)
	for _, p := range s.projects {
		if p.TenantID == tenantID && p.ID > page.Cursor {
			projects = append(projects, p)
		}
	}
	sort.Slice(projects, func(i, j int) bool { return projects[i].ID < projects[j].ID })

	result := ProjectPage{Items: projects}
	if len(result.Items) > page.Limit {
		result.Items = result.Items[:page.Limit]
		result.NextCursor = result.Items[len(result.Items)-1].ID
	}
	return result, nil
}

func (s *Store) Project(_ context.Context, tenantID, projectID string) (Project, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	p, ok := s.projects[projectKey(tenantID, projectID)]
	if !ok {
		return Project{}, ErrProjectNotFound
	}
	return p, nil
}

func (s *Store) UpdateProject(_ context.Context, p Project) (Project, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	key := projectKey(p.TenantID, p.ID)
	existing, ok := s.projects[key]
	if !ok {
		return Project{}, ErrProjectNotFound
	}
	existing.Name = strings.TrimSpace(p.Name)
	existing.UpdatedAt = time.Now().UTC()
	event, err := AuditProjectUpdated(existing)
	if err != nil {
		return Project{}, err
	}
	s.projects[key] = existing
	s.appendOutboxLocked(event)
	return existing, nil
}

func (s *Store) DeleteProject(_ context.Context, tenantID, projectID string) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	key := projectKey(tenantID, projectID)
	if _, ok := s.projects[key]; !ok {
		return ErrProjectNotFound
	}
	event, err := AuditProjectDeleted(tenantID, projectID)
	if err != nil {
		return err
	}
	delete(s.projects, key)
	s.appendOutboxLocked(event)
	return nil
}

func (s *Store) CreateAPIKey(_ context.Context, tenantID, name, scope string) (APIKey, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	key, err := s.createAPIKeyLocked(tenantID, name, scope)
	if err != nil {
		return APIKey{}, err
	}
	event, err := AuditAPIKeyCreated(key)
	if err != nil {
		delete(s.apiKeys, key.ID)
		return APIKey{}, err
	}
	s.appendOutboxLocked(event)
	return key, nil
}

func (s *Store) ListAPIKeys(_ context.Context, tenantID string) ([]APIKey, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	if _, ok := s.tenants[tenantID]; !ok {
		return nil, ErrTenantNotFound
	}
	out := make([]APIKey, 0)
	for _, rec := range s.apiKeys {
		if rec.key.TenantID == tenantID {
			out = append(out, rec.key)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].CreatedAt.Before(out[j].CreatedAt) })
	return out, nil
}

func (s *Store) RevokeAPIKey(_ context.Context, tenantID, keyID string) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	if err := s.revokeAPIKeyLocked(tenantID, keyID); err != nil {
		return err
	}
	event, err := AuditAPIKeyRevoked(tenantID, keyID)
	if err != nil {
		return err
	}
	s.appendOutboxLocked(event)
	return nil
}

func (s *Store) RotateAPIKey(_ context.Context, tenantID, keyID, name string) (APIKey, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	rec, ok := s.apiKeys[keyID]
	if !ok || rec.key.TenantID != tenantID || rec.key.RevokedAt != nil {
		return APIKey{}, ErrAPIKeyNotFound
	}
	if strings.TrimSpace(name) == "" {
		name = rec.key.Name
	}
	// Rotation replaces the credential, never its authority.
	key, err := s.createAPIKeyLocked(tenantID, name, rec.key.Scope)
	if err != nil {
		return APIKey{}, err
	}
	if err := s.revokeAPIKeyLocked(tenantID, keyID); err != nil {
		delete(s.apiKeys, key.ID)
		return APIKey{}, err
	}
	event, err := AuditAPIKeyRotated(key, keyID)
	if err != nil {
		delete(s.apiKeys, key.ID)
		return APIKey{}, err
	}
	s.appendOutboxLocked(event)
	return key, nil
}

func (s *Store) AuthenticateAPIKey(_ context.Context, tenantID, secret string) (APIKey, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()

	hash := HashAPIKey(secret)
	for _, rec := range s.apiKeys {
		if rec.key.TenantID == tenantID && rec.key.RevokedAt == nil && rec.hash == hash {
			return rec.key, nil
		}
	}
	return APIKey{}, ErrUnauthorized
}

func (s *Store) createAPIKeyLocked(tenantID, name, scope string) (APIKey, error) {
	if _, ok := s.tenants[tenantID]; !ok {
		return APIKey{}, ErrTenantNotFound
	}
	key, hash, err := NewRandomAPIKey(tenantID, name, scope)
	if err != nil {
		return APIKey{}, err
	}
	s.apiKeys[key.ID] = apiKeyRecord{key: KeyWithoutSecret(key), hash: hash}
	return key, nil
}

func (s *Store) revokeAPIKeyLocked(tenantID, keyID string) error {
	rec, ok := s.apiKeys[keyID]
	if !ok || rec.key.TenantID != tenantID {
		return ErrAPIKeyNotFound
	}
	if rec.key.RevokedAt == nil {
		now := time.Now().UTC()
		rec.key.RevokedAt = &now
		s.apiKeys[keyID] = rec
	}
	return nil
}

func NormalizeTenant(t Tenant) Tenant {
	t.ID = strings.TrimSpace(t.ID)
	t.Name = strings.TrimSpace(t.Name)
	if t.CreatedAt.IsZero() {
		t.CreatedAt = time.Now().UTC()
	}
	if t.Plan == "" {
		t.Plan = "standard"
	}
	if t.IsolationMode == "" {
		t.IsolationMode = "pool"
	}
	return t
}

func NormalizeProject(p Project) Project {
	p.ID = strings.TrimSpace(p.ID)
	p.Name = strings.TrimSpace(p.Name)
	p.TenantID = strings.TrimSpace(p.TenantID)
	if p.CreatedAt.IsZero() {
		p.CreatedAt = time.Now().UTC()
	}
	if p.UpdatedAt.IsZero() {
		p.UpdatedAt = p.CreatedAt
	}
	return p
}

func NormalizePageRequest(page PageRequest) PageRequest {
	page.Cursor = strings.TrimSpace(page.Cursor)
	if page.Limit <= 0 {
		page.Limit = DefaultPageSize
	}
	if page.Limit > MaxPageSize {
		page.Limit = MaxPageSize
	}
	return page
}

func NewRandomAPIKey(tenantID, name, scope string) (APIKey, string, error) {
	scope = NormalizeScope(scope)
	if !ValidScope(scope) {
		return APIKey{}, "", fmt.Errorf("invalid API key scope %q", scope)
	}
	raw := make([]byte, 24)
	if _, err := rand.Read(raw); err != nil {
		return APIKey{}, "", err
	}
	// The ID is drawn independently of the secret. IDs appear in URLs,
	// listings, and logs; deriving the ID from the secret's bytes would leak
	// part of the secret's entropy into every one of those places.
	id := make([]byte, 8)
	if _, err := rand.Read(id); err != nil {
		return APIKey{}, "", err
	}
	secret := "pf_live_" + hex.EncodeToString(raw)
	key := APIKey{
		ID:        "key_" + hex.EncodeToString(id),
		TenantID:  tenantID,
		Name:      strings.TrimSpace(name),
		Scope:     scope,
		Prefix:    prefix(secret),
		Secret:    secret,
		CreatedAt: time.Now().UTC(),
	}
	if key.Name == "" {
		key.Name = "default"
	}
	return key, HashAPIKey(secret), nil
}

func HashAPIKey(secret string) string {
	sum := sha256.Sum256([]byte(secret))
	return hex.EncodeToString(sum[:])
}

func KeyWithoutSecret(key APIKey) APIKey {
	key.Secret = ""
	return key
}

func projectKey(tenantID, projectID string) string {
	return tenantID + "/" + projectID
}

func prefix(secret string) string {
	if len(secret) <= 12 {
		return secret
	}
	return secret[:12]
}
