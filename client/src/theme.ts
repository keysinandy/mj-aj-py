import { useCallback, useEffect, useState } from "react";

export type ThemeMode = "light" | "dark";

export const THEME_STORAGE_KEY = "mj-aj-client-theme";

function isThemeMode(value: string | null): value is ThemeMode {
  return value === "light" || value === "dark";
}

function systemTheme(): ThemeMode {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return "light";
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function storedTheme(): ThemeMode | null {
  if (typeof window === "undefined") return null;
  try {
    const value = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isThemeMode(value) ? value : null;
  } catch {
    return null;
  }
}

export function resolveTheme(): ThemeMode {
  return storedTheme() ?? systemTheme();
}

export function applyTheme(theme: ThemeMode): void {
  if (typeof document === "undefined") return;
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
}

export function persistTheme(theme: ThemeMode): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Storage can be unavailable in embedded/private browser contexts. The
    // in-memory theme still works for the current page.
  }
}

export function useTheme() {
  const [theme, setTheme] = useState<ThemeMode>(() => resolveTheme());

  useEffect(() => {
    applyTheme(theme);
    persistTheme(theme);
  }, [theme]);

  const toggleTheme = useCallback(() => {
    setTheme((current) => (current === "dark" ? "light" : "dark"));
  }, []);

  return { theme, setTheme, toggleTheme };
}
