package v1alpha1

import (
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// WorkloadProfileSpec is the desired state of a WorkloadProfile. The spec is
// deliberately thin: predictions are observations, so they live in status,
// written by the classifier sync loop — never by humans.
type WorkloadProfileSpec struct {
	// TenantRef names the Tenant this profile describes.
	// +kubebuilder:validation:MinLength=1
	TenantRef string `json:"tenantRef"`

	// HistoryLimit bounds how many past classifications status retains.
	// +kubebuilder:default=12
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:validation:Maximum=100
	HistoryLimit int32 `json:"historyLimit,omitempty"`
}

// ClassObservation is one classifier output at a point in time.
type ClassObservation struct {
	// Class is the predicted workload class from the 6-label taxonomy
	// (research/TAXONOMY.md), e.g. "crud-bursty" or "ai-cacheable".
	Class string `json:"class"`

	// Confidence is the classifier softmax probability, 0–1000 permille.
	// Stored as permille because CRD schemas reject floating point.
	// +kubebuilder:validation:Minimum=0
	// +kubebuilder:validation:Maximum=1000
	ConfidencePermille int32 `json:"confidencePermille"`

	// ObservedAt is when the classification window closed.
	ObservedAt metav1.Time `json:"observedAt"`
}

// WorkloadProfileStatus is the observed state of a WorkloadProfile.
type WorkloadProfileStatus struct {
	// PredictedClass is the most recent classifier output.
	// +optional
	PredictedClass string `json:"predictedClass,omitempty"`

	// ConfidencePermille is the confidence of PredictedClass, 0–1000.
	// +optional
	ConfidencePermille int32 `json:"confidencePermille,omitempty"`

	// History holds the most recent observations, newest first,
	// truncated to spec.historyLimit.
	// +optional
	History []ClassObservation `json:"history,omitempty"`

	// LastUpdated is when status was last written.
	// +optional
	LastUpdated *metav1.Time `json:"lastUpdated,omitempty"`
}

// +kubebuilder:object:root=true
// +kubebuilder:resource:scope=Cluster,shortName=pfwp
// +kubebuilder:subresource:status
// +kubebuilder:printcolumn:name="Tenant",type=string,JSONPath=`.spec.tenantRef`
// +kubebuilder:printcolumn:name="Class",type=string,JSONPath=`.status.predictedClass`
// +kubebuilder:printcolumn:name="Confidence",type=integer,JSONPath=`.status.confidencePermille`
// +kubebuilder:printcolumn:name="Age",type=date,JSONPath=`.metadata.creationTimestamp`

// WorkloadProfile records what the classifier believes about a tenant's traffic.
type WorkloadProfile struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   WorkloadProfileSpec   `json:"spec,omitempty"`
	Status WorkloadProfileStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// WorkloadProfileList contains a list of WorkloadProfile.
type WorkloadProfileList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []WorkloadProfile `json:"items"`
}

func init() {
	SchemeBuilder.Register(&WorkloadProfile{}, &WorkloadProfileList{})
}
