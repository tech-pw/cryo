# UI craft pass — motion, chrome, and surfaces

Short record of the Emil / Apple craft pass on the React UI. Not a redesign of every
page. Goal: one designed language instead of leftover generated dual-CSS.

## Shipped

- Overlay motion: dialogs and alert dialogs close with ease-out and a faster exit.
  Sheets exit at `--motion-fast` (no ease-in). Popovers and tooltips are origin-aware.
  Tooltips skip delay after the first (`skipDelayDuration={300}`). Command palette is
  instant (`animate-none`). Press feedback is `scale(0.97)` on buttons, tabs, settings
  nav, Ask Cryo tabs, and metric tiles.
- Chrome: lucide `Menu` in the topbar; user menu is a Radix DropdownMenu. Sidebar semantic
  tokens map to primitives in light and dark. Unused `.cryo-btn` / `.cryo-input` /
  `.cryo-dialog-*` / custom user-dropdown CSS removed from `web/src/components/shell/shell.css`.
- Headers: Home uses `PageHeader` titled “Home”. Operations hero is operator chrome
  (no gradient, no uppercase tracking). Settings nav is opaque. Styleguide title is
  “Styleguide”.
- Ask Cryo: floating action button removed (topbar already opens it). Resize uses
  pointer capture and ignores extra pointers.
- Hints: `HintLabel` and cost metric hints use the Tooltip primitive, not native
  `title` or unicode ⓘ.
- Hover: `.stat-strip` lift is gated behind `(hover: hover) and (pointer: fine)`.

- Leftover pass: `@radix-ui/react-dropdown-menu` primitive with origin-aware motion.
  Topbar user menu uses it. Table-detail mobile tabs use a designed dropdown instead of
  native `<select>`. Truncated names and metric labels use Tooltip, not native `title`.
- Catalog explorer tables list: Table column defaults to content-like width (not 320px min /
  leftover stretch). Headers expose a hover-gated resize handle; widths persist in localStorage.
  Other DataTables, including the Catalogs summary, keep the previous stretch layout.

## Left for later

- Remaining page-level visual polish beyond this chrome/motion pass.
