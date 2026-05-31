package synthlimit

import "context"

// Limiter bounds concurrent synthesis (HTTP stream/async + live WS sessions).
type Limiter struct {
	ch chan struct{}
}

func New(max int) *Limiter {
	if max <= 0 {
		max = 1
	}
	return &Limiter{ch: make(chan struct{}, max)}
}

func (l *Limiter) Acquire(ctx context.Context) error {
	if l == nil {
		return nil
	}
	select {
	case l.ch <- struct{}{}:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	}
}

func (l *Limiter) Release() {
	if l == nil {
		return
	}
	<-l.ch
}
