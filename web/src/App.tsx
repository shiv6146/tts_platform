import { useState } from "react";
import { isLoggedIn } from "./lib/auth";
import { LoginPage } from "./pages/LoginPage";
import { RegisterPage } from "./pages/RegisterPage";
import { StudioPage } from "./pages/StudioPage";

export default function App() {
  const [authed, setAuthed] = useState(isLoggedIn());
  const [authView, setAuthView] = useState<"login" | "register">("login");

  if (!authed) {
    if (authView === "register") {
      return (
        <RegisterPage
          onSuccess={() => setAuthed(true)}
          onLogin={() => setAuthView("login")}
        />
      );
    }
    return (
      <LoginPage
        onSuccess={() => setAuthed(true)}
        onRegister={() => setAuthView("register")}
      />
    );
  }

  return <StudioPage onLogout={() => setAuthed(false)} />;
}
