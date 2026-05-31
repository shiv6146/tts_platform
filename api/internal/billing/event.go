package billing

import (
	"encoding/json"
	"time"

	"github.com/google/uuid"
)

const Subject = "billable.tts.v1"

type Event struct {
	IdempotencyKey string    `json:"idempotency_key"`
	UserID         string    `json:"user_id"`
	RequestID      string    `json:"request_id"`
	Transport      string    `json:"transport"`
	AudioSeconds   float64   `json:"audio_seconds"`
	OccurredAt     time.Time `json:"occurred_at"`
}

func (e Event) Marshal() ([]byte, error) {
	return json.Marshal(e)
}

type Publisher struct {
	nc interface {
		Publish(subj string, data []byte) error
	}
}

func NewPublisher(nc interface{ Publish(subj string, data []byte) error }) *Publisher {
	return &Publisher{nc: nc}
}

func (p *Publisher) Publish(e Event) error {
	if e.OccurredAt.IsZero() {
		e.OccurredAt = time.Now().UTC()
	}
	b, err := e.Marshal()
	if err != nil {
		return err
	}
	return p.nc.Publish(Subject, b)
}

type DeliveryBudget struct {
	RemainingUSD float64
	PricePerMin  float64
}

func (d DeliveryBudget) CanDeliverMore() bool {
	return d.RemainingUSD > 0
}

func CostForSeconds(seconds, pricePerMinute float64) float64 {
	if seconds <= 0 {
		return 0
	}
	return (seconds / 60.0) * pricePerMinute
}

func SecondsFromPCM(bytes int, sampleRate int) float64 {
	if sampleRate <= 0 {
		sampleRate = 24000
	}
	samples := bytes / 2
	return float64(samples) / float64(sampleRate)
}

func NewEvent(userID uuid.UUID, requestID, transport, idempotencyKey string, audioSeconds float64) Event {
	return Event{
		IdempotencyKey: idempotencyKey,
		UserID:         userID.String(),
		RequestID:      requestID,
		Transport:      transport,
		AudioSeconds:   audioSeconds,
		OccurredAt:     time.Now().UTC(),
	}
}
