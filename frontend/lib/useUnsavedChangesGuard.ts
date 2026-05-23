// useUnsavedChangesGuard — prompts the user before navigating away from a
// dirty form. Hooks into:
//   1. `beforeunload`: catches tab-close, refresh, and external-URL nav.
//   2. Next.js `routeChangeStart`: catches every in-app navigation
//      (Link clicks, router.push, browser back/forward).
//
// On routeChangeStart, the only way Next.js exposes cancellation is by
// throwing inside the handler — that's documented and stable (see
// https://nextjs.org/docs/pages/api-reference/functions/use-router#routerevents).
// We emit `routeChangeError` first so any subscribers see the abort, then
// throw a sentinel string that React/Next swallows without flooding the
// console with a stack trace.
//
// We use the browser-native `window.confirm` so the dialog blocks
// synchronously — async-confirm UIs (AlertDialog) don't work here because
// routeChangeStart can't await a Promise before deciding to cancel.

import { useEffect } from "react";
import { useRouter } from "next/router";

const ABORT_SENTINEL = "Route change aborted by unsaved-changes guard.";
const DEFAULT_MESSAGE = "You have unsaved changes. Discard them?";

export function useUnsavedChangesGuard(isDirty: boolean, message: string = DEFAULT_MESSAGE) {
  const router = useRouter();

  useEffect(() => {
    if (!isDirty) return;

    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      // Modern browsers ignore the returned string and show their own copy,
      // but `returnValue` must be set to trigger the prompt.
      e.preventDefault();
      e.returnValue = "";
    };

    const handleRouteChangeStart = () => {
      if (!window.confirm(message)) {
        router.events.emit("routeChangeError");
        // Next.js's documented cancellation pattern is to throw inside the
        // handler. Throwing a string (rather than an Error) is the form
        // Next.js's router recognizes and silently swallows; throwing an
        // Error subclass would print a stack trace to the console on every
        // aborted navigation.
        // eslint-disable-next-line no-throw-literal
        throw ABORT_SENTINEL;
      }
    };

    window.addEventListener("beforeunload", handleBeforeUnload);
    router.events.on("routeChangeStart", handleRouteChangeStart);

    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload);
      router.events.off("routeChangeStart", handleRouteChangeStart);
    };
  }, [isDirty, message, router]);
}
