// Package v1alpha1 contains the PolyForge operator API types.
//
// The four kinds — Tenant, WorkloadProfile, Policy, Budget — form the
// control-plane surface the JCAC controller acts through. Spec fields are
// desired state written by humans or the planner; status fields are observed
// state written only by the operator.
//
// +kubebuilder:object:generate=true
// +groupName=polyforge.io
package v1alpha1

import (
	"k8s.io/apimachinery/pkg/runtime/schema"
	"sigs.k8s.io/controller-runtime/pkg/scheme"
)

var (
	// GroupVersion is the group version used to register these objects.
	GroupVersion = schema.GroupVersion{Group: "polyforge.io", Version: "v1alpha1"}

	// SchemeBuilder is used to add go types to the GroupVersionKind scheme.
	SchemeBuilder = &scheme.Builder{GroupVersion: GroupVersion}

	// AddToScheme adds the types in this group-version to the given scheme.
	AddToScheme = SchemeBuilder.AddToScheme
)
