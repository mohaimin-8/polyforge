// Package saga implements orchestrated sagas per ADR 0006: a fixed sequence
// of idempotent steps, each with an explicit compensating action, with state
// persisted in the database rather than the broker. A crash mid-saga is
// recovered by re-executing the saga with the same ID: completed steps are
// skipped via the persisted cursor, and the in-flight step re-runs safely
// because every step is idempotent.
package saga

import (
	"context"
	"fmt"
	"log/slog"
	"sync"
	"time"
)

type Status string

const (
	StatusRunning     Status = "running"
	StatusCompleted   Status = "completed"
	StatusCompensated Status = "compensated"
	// StatusFailed means a step failed AND a compensation also failed, so
	// the system may hold partial state that needs manual repair.
	StatusFailed Status = "failed"
)

// State is the persisted saga cursor. StepsDone counts fully completed
// steps, so resume starts at steps[StepsDone].
type State struct {
	ID        string    `json:"id"`
	Name      string    `json:"name"`
	TenantID  string    `json:"tenant_id"`
	Status    Status    `json:"status"`
	StepsDone int       `json:"steps_done"`
	Error     string    `json:"error,omitempty"`
	UpdatedAt time.Time `json:"updated_at"`
}

// StateStore persists saga state. The SQLite store implements it in the
// same database as the domain rows; MemoryStateStore backs tests.
type StateStore interface {
	SaveSagaState(ctx context.Context, state State) error
	LoadSagaState(ctx context.Context, id string) (State, bool, error)
}

// Step is one unit of saga work. Run must be idempotent: it can be invoked
// again after a crash that lost its completion record. Compensate undoes
// the step's effect and must tolerate the step having only partially run.
type Step struct {
	Name       string
	Run        func(ctx context.Context) error
	Compensate func(ctx context.Context) error
}

type Saga struct {
	id       string
	name     string
	tenantID string
	states   StateStore
	steps    []Step
	log      *slog.Logger
}

func New(id, name, tenantID string, states StateStore, steps []Step, log *slog.Logger) *Saga {
	if log == nil {
		log = slog.Default()
	}
	return &Saga{id: id, name: name, tenantID: tenantID, states: states, steps: steps, log: log}
}

// Execute runs the saga to completion, resuming from persisted state when
// the same saga ID was started before. On a step failure it compensates all
// completed steps in reverse order and reports StatusCompensated.
func (s *Saga) Execute(ctx context.Context) (State, error) {
	state, found, err := s.states.LoadSagaState(ctx, s.id)
	if err != nil {
		return State{}, fmt.Errorf("load saga %s: %w", s.id, err)
	}
	if found && state.Status != StatusRunning {
		// Terminal states are sticky: re-running a finished saga is a no-op,
		// which is what makes crash-retry loops safe to point at Execute.
		return state, nil
	}
	if !found {
		state = State{ID: s.id, Name: s.name, TenantID: s.tenantID, Status: StatusRunning}
	}

	for i := state.StepsDone; i < len(s.steps); i++ {
		step := s.steps[i]
		if err := step.Run(ctx); err != nil {
			s.log.Warn("saga step failed; compensating",
				"saga", s.name, "saga_id", s.id, "step", step.Name, "error", err)
			return s.compensate(ctx, state, i, err)
		}
		state.StepsDone = i + 1
		if err := s.save(ctx, state); err != nil {
			return state, err
		}
	}
	state.Status = StatusCompleted
	if err := s.save(ctx, state); err != nil {
		return state, err
	}
	return state, nil
}

func (s *Saga) compensate(ctx context.Context, state State, failedStep int, cause error) (State, error) {
	state.Error = fmt.Sprintf("step %s: %v", s.steps[failedStep].Name, cause)
	// The failed step may have partially executed, so its own compensation
	// runs too, then every completed step's, newest first.
	for i := failedStep; i >= 0; i-- {
		if s.steps[i].Compensate == nil {
			continue
		}
		if err := s.steps[i].Compensate(ctx); err != nil {
			state.Status = StatusFailed
			state.Error += fmt.Sprintf("; compensate %s: %v", s.steps[i].Name, err)
			if saveErr := s.save(ctx, state); saveErr != nil {
				return state, saveErr
			}
			return state, fmt.Errorf("saga %s: %s", s.name, state.Error)
		}
	}
	state.Status = StatusCompensated
	state.StepsDone = 0
	if err := s.save(ctx, state); err != nil {
		return state, err
	}
	return state, cause
}

func (s *Saga) save(ctx context.Context, state State) error {
	state.UpdatedAt = time.Now().UTC()
	if err := s.states.SaveSagaState(ctx, state); err != nil {
		return fmt.Errorf("save saga %s: %w", s.id, err)
	}
	return nil
}

// MemoryStateStore is the in-memory StateStore for tests and the memory
// tenant store.
type MemoryStateStore struct {
	mu     sync.Mutex
	states map[string]State
}

func NewMemoryStateStore() *MemoryStateStore {
	return &MemoryStateStore{states: map[string]State{}}
}

func (m *MemoryStateStore) SaveSagaState(_ context.Context, state State) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.states[state.ID] = state
	return nil
}

func (m *MemoryStateStore) LoadSagaState(_ context.Context, id string) (State, bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	state, ok := m.states[id]
	return state, ok, nil
}
