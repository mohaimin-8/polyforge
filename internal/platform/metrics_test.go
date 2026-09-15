package platform

import "testing"

// Session 48: the CRUD path serves at 1-2 ms; a first boundary of 5 ms put
// every CRUD request in one bucket and made the in-run histogram's p95 the
// bucket edge. The histogram must resolve below 5 ms and keep the coarse
// tail for the AI path.
func TestRequestDurationHistogramResolvesBelowFiveMilliseconds(t *testing.T) {
	below := 0
	for _, b := range requestDurationBucketsSec {
		if b < 0.005 {
			below++
		}
	}
	if below < 3 {
		t.Fatalf("only %d boundaries below 5 ms: a 1-2 ms path is not measured", below)
	}
	for i := 1; i < len(requestDurationBucketsSec); i++ {
		if requestDurationBucketsSec[i] <= requestDurationBucketsSec[i-1] {
			t.Fatalf("boundaries must ascend: %v", requestDurationBucketsSec)
		}
	}
	last := requestDurationBucketsSec[len(requestDurationBucketsSec)-1]
	if last < 10 {
		t.Fatalf("the coarse tail for the AI path is gone: last boundary %v s", last)
	}
}
