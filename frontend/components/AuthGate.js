"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getCurrentUser } from "@/lib/api";

export default function AuthGate({ children }) {
  const router = useRouter();
  const [status, setStatus] = useState("checking");
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    getCurrentUser()
      .then((user) => {
        if (!active) return;
        if (user.authenticated) {
          setStatus("authenticated");
        } else {
          router.replace("/login");
        }
      })
      .catch((requestError) => {
        if (!active) return;
        setError(requestError.message);
        setStatus("error");
      });

    return () => {
      active = false;
    };
  }, [router]);

  if (status === "authenticated") return children;
  if (status === "error") {
    return (
      <div className="container page auth-check">
        <div className="alert" role="alert">{error}</div>
        <button className="btn primary" onClick={() => window.location.reload()}>Try again</button>
      </div>
    );
  }
  return <div className="container page auth-check muted">Checking your login…</div>;
}
