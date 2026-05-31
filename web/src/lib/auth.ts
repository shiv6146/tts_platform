const API_KEY = "tts_apiKey";
const USERNAME = "tts_username";

export function getApiKey(): string | null {
  return sessionStorage.getItem(API_KEY);
}

export function setSession(apiKey: string, username: string) {
  sessionStorage.setItem(API_KEY, apiKey);
  sessionStorage.setItem(USERNAME, username);
}

export function getUsername(): string | null {
  return sessionStorage.getItem(USERNAME);
}

export function clearSession() {
  sessionStorage.removeItem(API_KEY);
  sessionStorage.removeItem(USERNAME);
}

export function isLoggedIn(): boolean {
  return !!getApiKey();
}
