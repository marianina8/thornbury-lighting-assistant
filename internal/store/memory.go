package store

import (
	"context"
	"sort"
	"sync"
	"time"
)

// Memory is an in-process Store with the same semantics as Dynamo.
type Memory struct {
	mu     sync.Mutex
	keys   map[string]Key
	global map[string]int
	audit  map[string]Audit
}

func NewMemory() *Memory {
	return &Memory{keys: map[string]Key{}, global: map[string]int{}, audit: map[string]Audit{}}
}

func (m *Memory) GetKey(_ context.Context, hash string) (*Key, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	k, ok := m.keys[hash]
	if !ok {
		return nil, nil
	}
	return &k, nil
}

func (m *Memory) Reserve(_ context.Context, hash, period string, now time.Time) (Reservation, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	k, ok := m.keys[hash]
	switch {
	case !ok:
		return NoSuchKey, nil
	case !k.Active:
		return Revoked, nil
	}
	if k.Period != period {
		k.Period, k.Used = period, 0
	}
	if k.Used >= k.MonthlyLimit {
		return QuotaExceeded, nil
	}
	k.Used++
	k.LastUsedAt = now.UTC().Format(time.RFC3339)
	m.keys[hash] = k
	return Reserved, nil
}

func (m *Memory) Refund(_ context.Context, hash, period string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if k, ok := m.keys[hash]; ok && k.Period == period && k.Used > 0 {
		k.Used--
		m.keys[hash] = k
	}
	return nil
}

func (m *Memory) ReserveGlobal(_ context.Context, day string, limit int, _ time.Time) (bool, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.global[day] >= limit {
		return false, nil
	}
	m.global[day]++
	return true, nil
}

func (m *Memory) RefundGlobal(_ context.Context, day string) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.global[day] > 0 {
		m.global[day]--
	}
	return nil
}

func (m *Memory) PutAudit(_ context.Context, a Audit) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.audit[a.RequestID] = a
	return nil
}

func (m *Memory) GetAudit(_ context.Context, id string) (*Audit, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	a, ok := m.audit[id]
	if !ok {
		return nil, nil
	}
	return &a, nil
}

func (m *Memory) SetOutcome(_ context.Context, requestID, keyID, outcome, appliedJSON string, at time.Time) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	a, ok := m.audit[requestID]
	if !ok || a.KeyID != keyID {
		return ErrNotFound // never reveal another key's request
	}
	if a.Outcome != "" || a.Status != StatusProposed {
		return ErrConflict
	}
	a.Outcome, a.AppliedValues, a.OutcomeAt = outcome, appliedJSON, at.UTC().Format(time.RFC3339Nano)
	m.audit[requestID] = a
	return nil
}

func (m *Memory) PutKey(_ context.Context, k Key) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if _, ok := m.keys[k.Hash]; ok {
		return ErrExists
	}
	m.keys[k.Hash] = k
	return nil
}

func (m *Memory) ListKeys(_ context.Context) ([]Key, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make([]Key, 0, len(m.keys))
	for _, k := range m.keys {
		out = append(out, k)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].CreatedAt < out[j].CreatedAt })
	return out, nil
}

func (m *Memory) SetKeyLimit(_ context.Context, hash string, limit int) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	k, ok := m.keys[hash]
	if !ok {
		return ErrNotFound
	}
	k.MonthlyLimit = limit
	m.keys[hash] = k
	return nil
}

func (m *Memory) RevokeKey(_ context.Context, hash string, at time.Time) error {
	m.mu.Lock()
	defer m.mu.Unlock()
	k, ok := m.keys[hash]
	if !ok {
		return ErrNotFound
	}
	k.Active, k.RevokedAt = false, at.UTC().Format(time.RFC3339)
	m.keys[hash] = k
	return nil
}

func (m *Memory) RecentAudit(_ context.Context, keyID string, limit int) ([]Audit, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	var out []Audit
	for _, a := range m.audit {
		if keyID == "" || a.KeyID == keyID {
			out = append(out, a)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].CreatedAt > out[j].CreatedAt })
	if len(out) > limit {
		out = out[:limit]
	}
	return out, nil
}
