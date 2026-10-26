/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Release tag baked in by web/Dockerfile.prod (#263). Unset in dev. */
  readonly VITE_CRYO_VERSION?: string;
  readonly VITE_CRYO_GIT_SHA?: string;
}
