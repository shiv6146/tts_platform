package grpcclient

import (
	"context"
	"fmt"
	"io"
	"time"

	ttsv1 "github.com/tts-platform/api/internal/grpc/tts/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

type Client struct {
	conn   *grpc.ClientConn
	client ttsv1.TTSInferenceClient
}

func Dial(ctx context.Context, addr string) (*Client, error) {
	dialCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	conn, err := grpc.DialContext(dialCtx, addr,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithBlock(),
	)
	if err != nil {
		return nil, fmt.Errorf("grpc dial %s: %w", addr, err)
	}
	return &Client{conn: conn, client: ttsv1.NewTTSInferenceClient(conn)}, nil
}

func (c *Client) Close() error {
	return c.conn.Close()
}

func (c *Client) Health(ctx context.Context) error {
	resp, err := c.client.Health(ctx, &ttsv1.HealthRequest{})
	if err != nil {
		return err
	}
	if !resp.Ok {
		return fmt.Errorf("inference unhealthy")
	}
	return nil
}

func (c *Client) Synthesize(ctx context.Context, requestID, text, voice string) (grpc.ServerStreamingClient[ttsv1.AudioChunk], error) {
	return c.client.Synthesize(ctx, &ttsv1.SynthesizeRequest{
		RequestId: requestID,
		Text:      text,
		Voice:     voice,
	})
}

type LiveStream struct {
	stream grpc.BidiStreamingClient[ttsv1.LiveTextChunk, ttsv1.AudioChunk]
}

func (c *Client) SynthesizeLive(ctx context.Context) (*LiveStream, error) {
	stream, err := c.client.SynthesizeLive(ctx)
	if err != nil {
		return nil, err
	}
	return &LiveStream{stream: stream}, nil
}

func (ls *LiveStream) SendText(requestID, text, voice string, final bool) error {
	return ls.stream.Send(&ttsv1.LiveTextChunk{
		RequestId: requestID,
		Text:      text,
		Voice:     voice,
		Final:     final,
	})
}

func (ls *LiveStream) Recv() (*ttsv1.AudioChunk, error) {
	return ls.stream.Recv()
}

func (ls *LiveStream) CloseSend() error {
	return ls.stream.CloseSend()
}

func CopyAudioStream(ctx context.Context, stream grpc.ServerStreamingClient[ttsv1.AudioChunk], onChunk func(pcm []byte, sampleRate int32, seq int64) error) error {
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
		chunk, err := stream.Recv()
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
		if err := onChunk(chunk.Pcm, chunk.SampleRate, chunk.Seq); err != nil {
			return err
		}
	}
}
