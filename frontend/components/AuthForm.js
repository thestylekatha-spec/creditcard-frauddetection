"use client";
import Link from "next/link";
import { useState } from "react";
import { getCurrentUser, login, register } from "@/lib/api";

export default function AuthForm({ mode }) {
  const isRegister = mode === "register";
  const [form, setForm] = useState({ name: "", email: "", password: "" });
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState({});
  const [loading, setLoading] = useState(false);

  function update(field) {
    return (event) => setForm((current) => ({ ...current, [field]: event.target.value }));
  }

  async function onSubmit(event) {
    event.preventDefault();
    setError("");
    setFieldErrors({});
    setLoading(true);
    try {
      await (isRegister ? register(form) : login({ email: form.email, password: form.password }));
      const user = await getCurrentUser();
      if (!user.authenticated) {
        throw new Error("Login succeeded, but your session could not be verified. Please try again.");
      }
      window.location.assign("/");
    } catch (requestError) {
      setError(requestError.message);
      setFieldErrors(requestError.fields || {});
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="container page auth-page">
      <section className="auth-card card">
        <p className="eyebrow">{isRegister ? "Get started" : "Welcome back"}</p>
        <h1 className="h1-sm">{isRegister ? "Create your account" : "Log in to FraudGuard"}</h1>
        <p className="muted">
          {isRegister ? "Use your name and email to create an account." : "Enter your email and password to continue."}
        </p>

        <form onSubmit={onSubmit} className="auth-form">
          {isRegister && (
            <label className="field">
              <span>Name</span>
              <input autoComplete="name" name="name" value={form.name} onChange={update("name")}
                required maxLength={100} />
              {fieldErrors.name && <small className="err">{fieldErrors.name}</small>}
            </label>
          )}
          <label className="field">
            <span>Email</span>
            <input type="email" autoComplete="email" name="email" value={form.email}
              onChange={update("email")} required maxLength={254} />
            {fieldErrors.email && <small className="err">{fieldErrors.email}</small>}
          </label>
          <label className="field">
            <span>Password</span>
            <input type="password" autoComplete={isRegister ? "new-password" : "current-password"}
              name="password" value={form.password} onChange={update("password")} required
              minLength={8} maxLength={128} />
            {isRegister && <small className="muted">Use at least 8 characters.</small>}
            {fieldErrors.password && <small className="err">{fieldErrors.password}</small>}
          </label>

          {error && <div className="alert" role="alert">{error}</div>}
          <button className="btn primary full" type="submit" disabled={loading}>
            {loading ? "Please wait…" : isRegister ? "Create account" : "Log in"}
          </button>
        </form>

        <p className="auth-switch">
          {isRegister ? "Already have an account?" : "New to FraudGuard?"}{" "}
          <Link href={isRegister ? "/login" : "/register"}>
            {isRegister ? "Log in" : "Create an account"}
          </Link>
        </p>
      </section>
    </div>
  );
}
