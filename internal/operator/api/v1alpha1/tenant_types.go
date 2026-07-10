package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// IsolationMode is the tenant isolation ladder from ADR 0008.
// +kubebuilder:validation:Enum=pool;bridge;silo
type IsolationMode string

const (
	IsolationPool   IsolationMode = "pool"
	IsolationBridge IsolationMode = "bridge"
	IsolationSilo   IsolationMode = "silo"
)

// SLOClass selects the latency/availability objective set from docs/SLO.md.
// +kubebuilder:validation:Enum=best-effort;standard;premium
type SLOClass string

const (
	SLOBestEffort SLOClass = "best-effort"
	SLOStandard   SLOClass = "standard"
	SLOPremium    SLOClass = "premium"
)

// TenantSpec is the desired state of a PolyForge tenant.
type TenantSpec struct {
	// DisplayName is a human-readable name shown in dashboards.
	// +optional
	DisplayName string `json:"displayName,omitempty"`

	// IsolationMode places the tenant on the pool -> bridge -> silo ladder.
	// The operator only ever promotes; demotion requires deleting the Tenant.
	// +kubebuilder:default=pool
	IsolationMode IsolationMode `json:"isolationMode"`

	// SLOClass selects the objective set the planner must satisfy.
	// +kubebuilder:default=standard
	SLOClass SLOClass `json:"sloClass"`
}

// TenantStatus is the observed state of a Tenant.
type TenantStatus struct {
	// Namespace is the per-tenant namespace the operator manages
	// (only set for bridge and silo tenants).
	// +optional
	Namespace string `json:"namespace,omitempty"`

	// ObservedGeneration is the spec generation last acted upon.
	// +optional
	ObservedGeneration int64 `json:"observedGeneration,omitempty"`

	// LastReconcileTime is when the operator last completed a reconcile.
	// +optional
	LastReconcileTime *metav1.Time `json:"lastReconcileTime,omitempty"`

	// Conditions follow the standard Kubernetes condition conventions;
	// the operator maintains the "Ready" condition.
	// +optional
	// +listType=map
	// +listMapKey=type
	Conditions []metav1.Condition `json:"conditions,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:resource:scope=Cluster,shortName=pft
// +kubebuilder:subresource:status
// +kubebuilder:printcolumn:name="Isolation",type=string,JSONPath=`.spec.isolationMode`
// +kubebuilder:printcolumn:name="SLO",type=string,JSONPath=`.spec.sloClass`
// +kubebuilder:printcolumn:name="Ready",type=string,JSONPath=`.status.conditions[?(@.type=="Ready")].status`
// +kubebuilder:printcolumn:name="Age",type=date,JSONPath=`.metadata.creationTimestamp`

// Tenant is a customer of the PolyForge platform.
type Tenant struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   TenantSpec   `json:"spec,omitempty"`
	Status TenantStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// TenantList contains a list of Tenant.
type TenantList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []Tenant `json:"items"`
}

func init() {
	SchemeBuilder.Register(&Tenant{}, &TenantList{})
}
