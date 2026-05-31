package billing

import (
	"fmt"
	"sync"
	"time"

	"github.com/google/uuid"
)

type Coalescer struct {
	mu        sync.Mutex
	windows   map[string]*window
	interval  time.Duration
	pub       *Publisher
	userID    uuid.UUID
	request   string
	transport string
	seq       int
}

type window struct {
	audioSeconds float64
	lastFlush    time.Time
}

func NewCoalescer(pub *Publisher, userID uuid.UUID, requestID, transport string, interval time.Duration) *Coalescer {
	return &Coalescer{
		windows:   make(map[string]*window),
		interval:  interval,
		pub:       pub,
		userID:    userID,
		request:   requestID,
		transport: transport,
	}
}

func (c *Coalescer) AddPCM(bytes int, sampleRate int) error {
	sec := SecondsFromPCM(bytes, sampleRate)
	if sec <= 0 {
		return nil
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	w := c.windows[c.request]
	if w == nil {
		w = &window{lastFlush: time.Now()}
		c.windows[c.request] = w
	}
	w.audioSeconds += sec
	if time.Since(w.lastFlush) >= c.interval {
		return c.flushLocked(w)
	}
	return nil
}

func (c *Coalescer) Flush() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	w := c.windows[c.request]
	if w == nil || w.audioSeconds <= 0 {
		return nil
	}
	return c.flushLocked(w)
}

func (c *Coalescer) flushLocked(w *window) error {
	sec := w.audioSeconds
	w.audioSeconds = 0
	w.lastFlush = time.Now()
	c.seq++
	idem := fmt.Sprintf("%s:%d", c.request, c.seq)
	return c.pub.Publish(NewEvent(c.userID, c.request, c.transport, idem, sec))
}
