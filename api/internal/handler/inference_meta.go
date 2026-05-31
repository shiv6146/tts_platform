package handler

import (
	"net/http"
)

type inferenceMetaResponse struct {
	GRPCAddr string `json:"grpcAddr"`
	Backend  string `json:"backend,omitempty"`
	Ready    bool   `json:"ready"`
}

// GetMetaInference reports which gRPC inference container the API uses (debug / ops).
func (s *Server) GetMetaInference(w http.ResponseWriter, r *http.Request) {
	ready, backend, err := s.Inference.HealthDetail(r.Context())
	resp := inferenceMetaResponse{
		GRPCAddr: s.Cfg.InferenceGRPCAddr,
		Backend:  backend,
		Ready:    ready && err == nil,
	}
	if err != nil {
		resp.Ready = false
	}
	writeJSON(w, http.StatusOK, resp)
}
