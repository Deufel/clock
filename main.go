// Command clock-go is a real-time task time-tracker with Google sign-in
// and per-session SSE streams.
package main

import (
	"log"
	"net/http"
	"os"
	"time"

	"github.com/Deufel/clock-go/internal/auth"
	"github.com/Deufel/clock-go/internal/handlers"
	"github.com/Deufel/clock-go/internal/state"
)

func envOr(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func main() {
	dbPath := envOr("DB_PATH", "/app/data/clock-go.db")
	port := envOr("PORT", "8000")
	cookieSecret := envOr("COOKIE_SECRET", "change-me-in-production")
	publicURL := os.Getenv("PUBLIC_URL") // e.g. "https://clock.deufel.dev"

	db, err := state.Open(dbPath)
	if err != nil {
		log.Fatalf("open db: %v", err)
	}
	defer db.Close()

	srv := &handlers.Server{
		DB:     db,
		Hub:    state.NewHub(),
		Signer: auth.NewSigner(cookieSecret),
		Google: auth.GoogleConfig{
			ClientID:     os.Getenv("GOOGLE_CLIENT_ID"),
			ClientSecret: os.Getenv("GOOGLE_CLIENT_SECRET"),
		},
		AdminEmail: os.Getenv("ADMIN_EMAIL"),
		PublicURL:  publicURL,
	}

	httpSrv := &http.Server{
		Addr:              ":" + port,
		Handler:           srv.Routes(),
		ReadHeaderTimeout: 5 * time.Second,
		// No WriteTimeout: SSE connections are long-lived.
	}

	log.Printf("clock-go listening on :%s (db=%s, publicURL=%s)", port, dbPath, publicURL)
	if err := httpSrv.ListenAndServe(); err != nil {
		log.Fatalf("listen: %v", err)
	}
}
