package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// ModelTier selects which LLM class the gateway routes to for a tenant.
// "none" means the tenant runs no AI traffic.
// +kubebuilder:validation:Enum=none;small;mid;large
type ModelTier string

const (
	ModelTierNone  ModelTier = "none"
	ModelTierSmall ModelTier = "small"
	ModelTierMid   ModelTier = "mid"
	ModelTierLarge ModelTier = "large"
)

// PlanSource records who authored the currently applied plan.
type PlanSource string

const (
	PlanSourceManual   PlanSource = "manual"
	PlanSourcePlanner  PlanSource = "planner"
	PlanSourceFallback PlanSource = "fallback"
)

// PolicySpec is the desired resource allocation for one tenant. The JCAC
// planner writes replicas/cacheSizeMB/modelTier; humans set the bounds.
type PolicySpec struct {
	// TenantRef names the Tenant this policy applies to.
	// +kubebuilder:validation:MinLength=1
	TenantRef string `json:"tenantRef"`

	// TargetDeployment is the Deployment the operator scales,
	// namespace/name. Defaults to the tenant's gateway deployment.
	// +optional
	TargetDeployment string `json:"targetDeployment,omitempty"`

	// Replicas is the desired replica count, clamped to [replicaMin, replicaMax].
	// +kubebuilder:default=2
	// +kubebuilder:validation:Minimum=0
	Replicas int32 `json:"replicas"`

	// ReplicaMin is the floor the planner may never cross (safety guardrail).
	// +kubebuilder:default=1
	// +kubebuilder:validation:Minimum=0
	ReplicaMin int32 `json:"replicaMin"`

	// ReplicaMax is the ceiling the planner may never cross.
	// +kubebuilder:default=10
	// +kubebuilder:validation:Minimum=1
	ReplicaMax int32 `json:"replicaMax"`

	// CacheSizeMB is the semantic-cache budget for this tenant (W28
	// bounds eviction per tenant; this is the knob JCAC turns).
	// +kubebuilder:default=128
	// +kubebuilder:validation:Minimum=0
	// +kubebuilder:validation:Maximum=16384
	CacheSizeMB int32 `json:"cacheSizeMB"`

	// ModelTier is the LLM class the gateway routes this tenant to.
	// +kubebuilder:default=small
	ModelTier ModelTier `json:"modelTier"`
}

// PolicyStatus is the observed state of a Policy.
type PolicyStatus struct {
	// AppliedReplicas is the replica count last written to the Deployment.
	// +optional
	AppliedReplicas int32 `json:"appliedReplicas,omitempty"`

	// AppliedCacheSizeMB is the cache budget last written to the ConfigMap.
	// +optional
	AppliedCacheSizeMB int32 `json:"appliedCacheSizeMB,omitempty"`

	// AppliedModelTier is the tier last written to the route table.
	// +optional
	AppliedModelTier ModelTier `json:"appliedModelTier,omitempty"`

	// LastPlanSource records whether the applied values came from the
	// planner, a fallback to the last good plan, or a human.
	// +optional
	LastPlanSource PlanSource `json:"lastPlanSource,omitempty"`

	// LastPlanTime is when the planner last produced the applied values.
	// +optional
	LastPlanTime *metav1.Time `json:"lastPlanTime,omitempty"`

	// ObservedGeneration is the spec generation last acted upon.
	// +optional
	ObservedGeneration int64 `json:"observedGeneration,omitempty"`

	// Conditions: the operator maintains "Applied".
	// +optional
	// +listType=map
	// +listMapKey=type
	Conditions []metav1.Condition `json:"conditions,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:resource:scope=Cluster,shortName=pfpol
// +kubebuilder:subresource:status
// +kubebuilder:printcolumn:name="Tenant",type=string,JSONPath=`.spec.tenantRef`
// +kubebuilder:printcolumn:name="Replicas",type=integer,JSONPath=`.spec.replicas`
// +kubebuilder:printcolumn:name="CacheMB",type=integer,JSONPath=`.spec.cacheSizeMB`
// +kubebuilder:printcolumn:name="Tier",type=string,JSONPath=`.spec.modelTier`
// +kubebuilder:printcolumn:name="Source",type=string,JSONPath=`.status.lastPlanSource`
// +kubebuilder:printcolumn:name="Age",type=date,JSONPath=`.metadata.creationTimestamp`

// Policy is the joint resource allocation JCAC controls for one tenant.
type Policy struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   PolicySpec   `json:"spec,omitempty"`
	Status PolicyStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// PolicyList contains a list of Policy.
type PolicyList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []Policy `json:"items"`
}

func init() {
	SchemeBuilder.Register(&Policy{}, &PolicyList{})
}
