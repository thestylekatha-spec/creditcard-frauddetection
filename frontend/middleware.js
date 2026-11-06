import { NextResponse } from "next/server";

const apiUrl = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:5000").replace(/\/+$/, "");

export async function middleware(request) {
  const sessionCookie = request.cookies.get("session");
  if (!sessionCookie) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  try {
    const response = await fetch(`${apiUrl}/api/auth/me`, {
      headers: { cookie: `session=${encodeURIComponent(sessionCookie.value)}` },
      cache: "no-store",
    });
    const user = await response.json();
    if (response.ok && user.authenticated) return NextResponse.next();
  } catch {
    // The client-side gate displays the API error instead of exposing the page.
    return NextResponse.next();
  }

  return NextResponse.redirect(new URL("/login", request.url));
}

export const config = {
  matcher: ["/", "/detect/:path*", "/dashboard/:path*"],
};
