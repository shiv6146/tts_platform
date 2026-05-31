package ui

import (
	"embed"
	"io/fs"
	"net/http"
	"strings"
)

//go:embed dist/*
var dist embed.FS

// Handler serves the SPA with index.html fallback for client-side routes.
func Handler() http.Handler {
	sub, err := fs.Sub(dist, "dist")
	if err != nil {
		return http.NotFoundHandler()
	}
	fileServer := http.FileServer(http.FS(sub))
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		path := strings.TrimPrefix(r.URL.Path, "/")
		if path == "" {
			path = "index.html"
		}
		if _, err := sub.Open(path); err != nil {
			http.ServeFileFS(w, r, sub, "index.html")
			return
		}
		fileServer.ServeHTTP(w, r)
	})
}
