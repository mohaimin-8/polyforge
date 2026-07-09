package eviction

import (
	"container/list"
	"time"
)

// ---- GDSF (GreedyDual-Size-Frequency, Cherkasova 1998) ----
//
// priority = L + frequency × cost / size. L is the inflation ("aging")
// value: when an entry is evicted, L rises to the victim's priority, so
// long-idle entries eventually lose to fresh ones regardless of cost.
// GDSF is the strongest classical baseline here because it already sees
// cost and size — what it lacks is hit-probability and semantic density.

type gdsfEntry struct {
	frequency float64
	cost      float64
	size      float64
	priority  float64
}

type gdsf struct {
	entries map[string]*gdsfEntry
	l       float64
}

func NewGDSF() Policy {
	return &gdsf{entries: map[string]*gdsfEntry{}}
}

func (p *gdsf) Name() string { return "gdsf" }

func (p *gdsf) OnStore(e Entry, _ time.Time) {
	size := float64(e.SizeBytes)
	if size <= 0 {
		size = 1
	}
	entry := &gdsfEntry{frequency: 1, cost: e.RecomputeCostUSD, size: size}
	entry.priority = p.l + entry.frequency*entry.cost/entry.size
	p.entries[e.ID] = entry
}

func (p *gdsf) OnHit(id string, _ time.Time) {
	if entry, ok := p.entries[id]; ok {
		entry.frequency++
		entry.priority = p.l + entry.frequency*entry.cost/entry.size
	}
}

func (p *gdsf) OnRemove(id string) {
	delete(p.entries, id)
}

func (p *gdsf) Victim(_ time.Time) (string, bool) {
	var victim string
	var found bool
	for id, entry := range p.entries {
		if !found || entry.priority < p.entries[victim].priority {
			victim, found = id, true
		}
	}
	if found {
		p.l = p.entries[victim].priority
	}
	return victim, found
}

// ---- ARC (Adaptive Replacement Cache, Megiddo & Modha 2003) ----
//
// Balances recency (T1) against frequency (T2) with ghost lists (B1, B2)
// steering the adaptation parameter. The textbook algorithm is defined
// over unit-size pages, so the benchmark runs every policy with an
// entry-count capacity to keep the ARC comparison faithful (size-aware
// policies still see sizes in their scores).

type arc struct {
	capacity int
	p        int
	t1, t2   *list.List // caches: front = MRU
	b1, b2   *list.List // ghosts
	where    map[string]*arcSlot
}

type arcSlot struct {
	list    *list.List
	element *list.Element
}

// NewARC needs the cache capacity (entries) to size its ghost lists.
func NewARC(capacity int) Policy {
	return &arc{
		capacity: capacity,
		t1:       list.New(), t2: list.New(),
		b1: list.New(), b2: list.New(),
		where: map[string]*arcSlot{},
	}
}

func (p *arc) Name() string { return "arc" }

func (p *arc) OnHit(id string, _ time.Time) {
	slot, ok := p.where[id]
	if !ok || (slot.list != p.t1 && slot.list != p.t2) {
		return
	}
	// Any repeat access promotes to the frequent list's MRU position.
	slot.list.Remove(slot.element)
	slot.list = p.t2
	slot.element = p.t2.PushFront(id)
}

func (p *arc) OnStore(e Entry, _ time.Time) {
	id := e.ID
	if slot, ok := p.where[id]; ok {
		switch slot.list {
		case p.b1:
			// Ghost hit in B1: recency was undervalued — grow p.
			p.p = min(p.p+max(1, p.b2.Len()/max(1, p.b1.Len())), p.capacity)
			slot.list.Remove(slot.element)
			slot.list = p.t2
			slot.element = p.t2.PushFront(id)
			return
		case p.b2:
			// Ghost hit in B2: frequency was undervalued — shrink p.
			p.p = max(p.p-max(1, p.b1.Len()/max(1, p.b2.Len())), 0)
			slot.list.Remove(slot.element)
			slot.list = p.t2
			slot.element = p.t2.PushFront(id)
			return
		default:
			// Already resident; treat as a refresh.
			p.OnHit(id, time.Time{})
			return
		}
	}
	// Brand new entry goes to the recency list.
	p.where[id] = &arcSlot{list: p.t1, element: p.t1.PushFront(id)}
	p.trimGhosts()
}

func (p *arc) trimGhosts() {
	// |T1 ∪ B1| ≤ c and total directory ≤ 2c, per the paper.
	for p.t1.Len()+p.b1.Len() > p.capacity && p.b1.Len() > 0 {
		p.dropGhost(p.b1)
	}
	for p.t1.Len()+p.t2.Len()+p.b1.Len()+p.b2.Len() > 2*p.capacity && p.b2.Len() > 0 {
		p.dropGhost(p.b2)
	}
}

func (p *arc) dropGhost(ghosts *list.List) {
	back := ghosts.Back()
	if back == nil {
		return
	}
	ghosts.Remove(back)
	delete(p.where, back.Value.(string))
}

// Victim implements REPLACE: evict T1's LRU when T1 exceeds the target p,
// else T2's LRU. The evicted entry becomes a ghost.
func (p *arc) Victim(_ time.Time) (string, bool) {
	var source, ghost *list.List
	if p.t1.Len() > 0 && (p.t1.Len() > p.p || p.t2.Len() == 0) {
		source, ghost = p.t1, p.b1
	} else if p.t2.Len() > 0 {
		source, ghost = p.t2, p.b2
	} else {
		return "", false
	}
	back := source.Back()
	id := back.Value.(string)
	source.Remove(back)
	p.where[id] = &arcSlot{list: ghost, element: ghost.PushFront(id)}
	p.trimGhosts()
	return id, true
}

func (p *arc) OnRemove(id string) {
	// The cache reports the eviction Victim already ghosted; only remove
	// entries that are still resident (external removal).
	slot, ok := p.where[id]
	if !ok || slot.list == p.b1 || slot.list == p.b2 {
		return
	}
	slot.list.Remove(slot.element)
	delete(p.where, id)
}
