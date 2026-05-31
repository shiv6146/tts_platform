package audio

import (
	"encoding/binary"
)

const SampleRate = 24000

func WrapWAV(pcm []byte, sampleRate int) []byte {
	if sampleRate <= 0 {
		sampleRate = SampleRate
	}
	dataSize := uint32(len(pcm))
	fileSize := 36 + dataSize
	hdr := make([]byte, 44)
	copy(hdr[0:4], "RIFF")
	binary.LittleEndian.PutUint32(hdr[4:8], fileSize)
	copy(hdr[8:12], "WAVE")
	copy(hdr[12:16], "fmt ")
	binary.LittleEndian.PutUint32(hdr[16:20], 16)
	binary.LittleEndian.PutUint16(hdr[20:22], 1)
	binary.LittleEndian.PutUint16(hdr[22:24], 1)
	binary.LittleEndian.PutUint32(hdr[24:28], uint32(sampleRate))
	byteRate := uint32(sampleRate * 2)
	binary.LittleEndian.PutUint32(hdr[28:32], byteRate)
	binary.LittleEndian.PutUint16(hdr[32:34], 2)
	binary.LittleEndian.PutUint16(hdr[34:36], 16)
	copy(hdr[36:40], "data")
	binary.LittleEndian.PutUint32(hdr[40:44], dataSize)
	out := make([]byte, 44+len(pcm))
	copy(out, hdr)
	copy(out[44:], pcm)
	return out
}
