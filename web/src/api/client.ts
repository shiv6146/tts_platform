import createClient from "openapi-fetch";
import type { paths } from "./schema";
import { clearSession, getApiKey } from "../lib/auth";

export const api = createClient<paths>({
  baseUrl: "",
  credentials: "include",
});

api.use({
  onRequest({ request }) {
    const key = getApiKey();
    if (key) {
      request.headers.set("Authorization", `Bearer ${key}`);
    }
    return request;
  },
});

export async function logout() {
  await api.POST("/v1/auth/logout", {});
  clearSession();
}

export type VoicesMeta = NonNullable<
  paths["/v1/meta/voices"]["get"]["responses"][200]["content"]["application/json"]
>;
