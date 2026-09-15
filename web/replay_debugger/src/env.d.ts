declare module "*.css" {
  const stylesheet: string;
  export default stylesheet;
}

declare module "react" {
  export type ReactNode = unknown;

  export interface ChangeEvent<T = Element> {
    target: T;
    currentTarget: T;
  }

  export interface HTMLAttributes<T> {
    children?: ReactNode;
    className?: string;
    [key: string]: unknown;
  }

  export interface ButtonHTMLAttributes<T> extends HTMLAttributes<T> {
    disabled?: boolean;
    type?: "button" | "submit" | "reset";
    onClick?: (event: unknown) => void;
  }

  export interface InputHTMLAttributes<T> extends HTMLAttributes<T> {
    type?: string;
    value?: string | number | readonly string[];
    min?: string | number;
    onChange?: (event: ChangeEvent<T>) => void;
  }

  export interface SelectHTMLAttributes<T> extends HTMLAttributes<T> {
    value?: string | number | readonly string[];
    onChange?: (event: ChangeEvent<T>) => void;
  }

  export function useEffect(
    effect: () => void | (() => void),
    deps?: readonly unknown[],
  ): void;
  export function useMemo<T>(factory: () => T, deps: readonly unknown[]): T;
  export function useState<T>(
    initial: T | (() => T),
  ): [T, (value: T | ((previous: T) => T)) => void];
  export function StrictMode(props: { children?: ReactNode }): JSX.Element;
}

declare module "react/jsx-runtime" {
  export const Fragment: unknown;
  export function jsx(...args: unknown[]): unknown;
  export function jsxs(...args: unknown[]): unknown;
}

declare module "react-dom/client" {
  export function createRoot(
    container: Element | DocumentFragment | null,
  ): { render(children: unknown): void };
}

declare namespace JSX {
  interface Element {}
  interface IntrinsicElements {
    [elementName: string]: any;
  }
}
