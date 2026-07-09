package classifier

// Evaluation summarizes a classifier against labeled examples.
type Evaluation struct {
	Accuracy float64
	MacroF1  float64
	// Confusion[actual][predicted].
	Confusion map[Label]map[Label]int
	PerClass  map[Label]PRF
}

// PRF is per-class precision/recall/F1.
type PRF struct {
	Precision float64
	Recall    float64
	F1        float64
}

// Evaluate runs any predictor over the examples. Both the trained model
// and the W19 rule baseline evaluate through this one code path so their
// numbers are comparable by construction.
func Evaluate(predict func([FeatureCount]float64) Label, examples []Example) Evaluation {
	confusion := map[Label]map[Label]int{}
	correct := 0
	for _, ex := range examples {
		got := predict(ex.Features)
		if confusion[ex.Label] == nil {
			confusion[ex.Label] = map[Label]int{}
		}
		confusion[ex.Label][got]++
		if got == ex.Label {
			correct++
		}
	}
	eval := Evaluation{
		Accuracy:  float64(correct) / float64(len(examples)),
		Confusion: confusion,
		PerClass:  map[Label]PRF{},
	}
	var f1Sum float64
	for _, class := range Classes {
		var tp, fp, fn float64
		for _, actual := range Classes {
			for _, predicted := range Classes {
				count := float64(confusion[actual][predicted])
				switch {
				case actual == class && predicted == class:
					tp += count
				case actual != class && predicted == class:
					fp += count
				case actual == class && predicted != class:
					fn += count
				}
			}
		}
		prf := PRF{}
		if tp+fp > 0 {
			prf.Precision = tp / (tp + fp)
		}
		if tp+fn > 0 {
			prf.Recall = tp / (tp + fn)
		}
		if prf.Precision+prf.Recall > 0 {
			prf.F1 = 2 * prf.Precision * prf.Recall / (prf.Precision + prf.Recall)
		}
		eval.PerClass[class] = prf
		f1Sum += prf.F1
	}
	eval.MacroF1 = f1Sum / float64(len(Classes))
	return eval
}
