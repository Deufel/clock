// Package auth implements HMAC-signed session cookies and the Google OAuth
// authorization-code flow. State CSRF is enforced via a signed cookie that
// holds the random "state" value sent to Google.
package auth

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// ---- Signer ----------------------------------------------------------------

type Signer struct {
	key []byte
}

func NewSigner(secret string) *Signer {
	return &Signer{key: []byte(secret)}
}

// Sign returns "value.signature" where signature is base64(HMAC-SHA256).
func (s *Signer) Sign(value string) string {
	mac := hmac.New(sha256.New, s.key)
	mac.Write([]byte(value))
	sig := base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
	return value + "." + sig
}

// Unsign verifies and returns the underlying value, or "" if invalid.
func (s *Signer) Unsign(raw string) string {
	i := strings.LastIndex(raw, ".")
	if i < 0 {
		return ""
	}
	value, gotSig := raw[:i], raw[i+1:]
	mac := hmac.New(sha256.New, s.key)
	mac.Write([]byte(value))
	wantSig := base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
	if !hmac.Equal([]byte(gotSig), []byte(wantSig)) {
		return ""
	}
	return value
}

// ---- Cookie helpers --------------------------------------------------------

// SetSignedCookie writes a signed cookie. Caller chooses name, value, MaxAge.
func (s *Signer) SetSignedCookie(w http.ResponseWriter, name, value string, maxAge int) {
	http.SetCookie(w, &http.Cookie{
		Name:     name,
		Value:    s.Sign(value),
		Path:     "/",
		HttpOnly: true,
		Secure:   true,
		SameSite: http.SameSiteLaxMode,
		MaxAge:   maxAge,
	})
}

// ReadSignedCookie returns the underlying value of a signed cookie, or "".
func (s *Signer) ReadSignedCookie(r *http.Request, name string) string {
	c, err := r.Cookie(name)
	if err != nil {
		return ""
	}
	return s.Unsign(c.Value)
}

// ClearCookie writes an immediately-expired cookie of the given name.
func ClearCookie(w http.ResponseWriter, name string) {
	http.SetCookie(w, &http.Cookie{
		Name:     name,
		Value:    "",
		Path:     "/",
		HttpOnly: true,
		Secure:   true,
		SameSite: http.SameSiteLaxMode,
		MaxAge:   -1,
	})
}

// ---- Google OAuth ----------------------------------------------------------

type GoogleConfig struct {
	ClientID     string
	ClientSecret string
}

type GoogleUserInfo struct {
	Sub   string `json:"sub"`
	Email string `json:"email"`
	Name  string `json:"name"`
}

// AuthorizeURL returns the URL to redirect the browser to. state is an opaque
// CSRF token the caller must verify in the callback.
func (g GoogleConfig) AuthorizeURL(redirectURI, state string) string {
	v := url.Values{}
	v.Set("client_id", g.ClientID)
	v.Set("redirect_uri", redirectURI)
	v.Set("response_type", "code")
	v.Set("scope", "email profile")
	v.Set("access_type", "online")
	v.Set("state", state)
	return "https://accounts.google.com/o/oauth2/v2/auth?" + v.Encode()
}

// Exchange swaps the auth code for an access token and fetches the user's
// profile. Returns the user info or an error describing where it failed.
func (g GoogleConfig) Exchange(code, redirectURI string) (*GoogleUserInfo, error) {
	form := url.Values{}
	form.Set("client_id", g.ClientID)
	form.Set("client_secret", g.ClientSecret)
	form.Set("code", code)
	form.Set("redirect_uri", redirectURI)
	form.Set("grant_type", "authorization_code")

	client := &http.Client{Timeout: 10 * time.Second}

	tokResp, err := client.PostForm("https://oauth2.googleapis.com/token", form)
	if err != nil {
		return nil, fmt.Errorf("token request: %w", err)
	}
	defer tokResp.Body.Close()
	if tokResp.StatusCode != 200 {
		body, _ := io.ReadAll(tokResp.Body)
		return nil, fmt.Errorf("token endpoint status %d: %s", tokResp.StatusCode, string(body))
	}
	var tok struct {
		AccessToken string `json:"access_token"`
	}
	if err := json.NewDecoder(tokResp.Body).Decode(&tok); err != nil {
		return nil, fmt.Errorf("token decode: %w", err)
	}
	if tok.AccessToken == "" {
		return nil, errors.New("empty access_token")
	}

	req, _ := http.NewRequest("GET", "https://www.googleapis.com/oauth2/v3/userinfo", nil)
	req.Header.Set("Authorization", "Bearer "+tok.AccessToken)
	uiResp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("userinfo request: %w", err)
	}
	defer uiResp.Body.Close()
	if uiResp.StatusCode != 200 {
		body, _ := io.ReadAll(uiResp.Body)
		return nil, fmt.Errorf("userinfo status %d: %s", uiResp.StatusCode, string(body))
	}
	var info GoogleUserInfo
	if err := json.NewDecoder(uiResp.Body).Decode(&info); err != nil {
		return nil, fmt.Errorf("userinfo decode: %w", err)
	}
	if info.Email == "" {
		return nil, errors.New("empty email in userinfo")
	}
	return &info, nil
}

// NewRandomState returns a fresh random token for use as the OAuth state
// parameter.
func NewRandomState() (string, error) {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}
