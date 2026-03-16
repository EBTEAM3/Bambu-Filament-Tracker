async function handleLogin(event) {
  event.preventDefault();
  const errorEl = document.getElementById("login-error");
  errorEl.textContent = "";

  const username = document.getElementById("username").value.trim();
  const password = document.getElementById("password").value;

  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const payload = await res.json();
    if (!res.ok) {
      errorEl.textContent = payload.error || "Error de autenticación";
      return;
    }
    globalThis.location.href = "/";
  } catch (err) {
    console.error("Login request failed", err);
    errorEl.textContent = "No se pudo conectar con el servidor";
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("login-form").addEventListener("submit", handleLogin);
});
