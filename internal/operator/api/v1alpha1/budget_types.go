package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// BudgetSpec caps what one tenant may spend and how much the planner should
// weight their fairness. Money is integer milli-USD because CRD schemas
// reject floating point and money as floats is a bug factory anyway.
type BudgetSpec struct {
	// TenantRef names the Tenant this budget applies to.
	// +kubebuilder:validation:MinLength=1
	TenantRef string `json:"tenantRef"`

	// HourlyCapMilliUSD is the hard hourly spend ceiling in milli-USD
	// (e.g. 2500 = $2.50/hour). The planner must never plan above it.
	// +kubebuilder:default=1000
	// +kubebuilder:validation:Minimum=0
	HourlyCapMilliUSD int64 `json:"hourlyCapMilliUSD"`

	// FairnessWeightPermille is this tenant's weight in the planner's
	// fairness penalty, 0–1000. Higher means the planner protects this
	// tenant's SLO more aggressively when tenants compete.
	// +kubebuilder:default=500
	// +kubebuilder:validation:Minimum=0
	// +kubebuilder:validation:Maximum=1000
	FairnessWeightPermille int32 `json:"fairnessWeightPermille"`
}

// BudgetStatus is the observed state of a Budget.
type BudgetStatus struct {
	// CurrentHourlySpendMilliUSD is the operator's latest spend estimate.
	// +optional
	CurrentHourlySpendMilliUSD int64 `json:"currentHourlySpendMilliUSD,omitempty"`

	// UtilizationPercent is spend/cap, 0–100+ (may exceed 100 briefly
	// between control cycles).
	// +optional
	UtilizationPercent int32 `json:"utilizationPercent,omitempty"`

	// LastUpdated is when status was last written.
	// +optional
	LastUpdated *metav1.Time `json:"lastUpdated,omitempty"`

	// Conditions: the operator maintains "WithinBudget".
	// +optional
	// +listType=map
	// +listMapKey=type
	Conditions []metav1.Condition `json:"conditions,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:resource:scope=Cluster,shortName=pfbud
// +kubebuilder:subresource:status
// +kubebuilder:printcolumn:name="Tenant",type=string,JSONPath=`.spec.tenantRef`
// +kubebuilder:printcolumn:name="CapMilliUSD",type=integer,JSONPath=`.spec.hourlyCapMilliUSD`
// +kubebuilder:printcolumn:name="Util%",type=integer,JSONPath=`.status.utilizationPercent`
// +kubebuilder:printcolumn:name="Age",type=date,JSONPath=`.metadata.creationTimestamp`

// Budget bounds one tenant's spend and fairness weight.
type Budget struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   BudgetSpec   `json:"spec,omitempty"`
	Status BudgetStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// BudgetList contains a list of Budget.
type BudgetList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []Budget `json:"items"`
}

func init() {
	SchemeBuilder.Register(&Budget{}, &BudgetList{})
}
