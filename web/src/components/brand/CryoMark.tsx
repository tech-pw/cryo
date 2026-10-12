import type { SVGProps } from "react";

const line = { stroke: "var(--cryo-mark-line, #143E60)" };

/** Ice-cube mark shared with the public docs site (`site/assets/svg/cryo-mark.svg`). */
export const CryoMark = ({ title, ...props }: SVGProps<SVGSVGElement> & { title?: string }) => (
  <svg viewBox="0 0 64 64" xmlns="http://www.w3.org/2000/svg" role={title ? "img" : "presentation"} {...props}>
    {title ? <title>{title}</title> : null}
    <g strokeWidth="1.6" strokeLinejoin="round" style={line}>
      <path d="M11 9L15.33 11.5L11 14L6.67 11.5Z" fill="#EAF7FD" />
      <path d="M6.67 11.5L11 14V19L6.67 16.5Z" fill="#68C1EA" />
      <path d="M11 14L15.33 11.5V16.5L11 19Z" fill="#277ABE" />
      <path d="M25 4L28.46 6L25 8L21.54 6Z" fill="#EAF7FD" />
      <path d="M21.54 6L25 8V12L21.54 10Z" fill="#68C1EA" />
      <path d="M25 8L28.46 6V10L25 12Z" fill="#277ABE" />
      <path d="M7 29L10.46 31L7 33L3.54 31Z" fill="#EAF7FD" />
      <path d="M3.54 31L7 33V37L3.54 35Z" fill="#68C1EA" />
      <path d="M7 33L10.46 31V35L7 37Z" fill="#277ABE" />
    </g>
    <g fill="none" strokeWidth="2" strokeLinecap="round" style={{ stroke: "var(--cryo-mark-dash, #5C667A)" }}>
      <path d="M17 19.5L20 22.3" />
      <path d="M27.6 14.5L29 18" />
      <path d="M13.9 34.1L17.2 34.7" />
    </g>
    <g strokeWidth="2.6" strokeLinejoin="round" style={line}>
      <path d="M37 19L53.45 28.5L37 38L20.55 28.5Z" fill="#EAF7FD" />
      <path d="M20.55 28.5L37 38V57L20.55 47.5Z" fill="#68C1EA" />
      <path d="M37 38L53.45 28.5V47.5L37 57Z" fill="#277ABE" />
    </g>
    <g fill="none" strokeWidth="1.1">
      <path d="M45.23 23.75L28.78 33.25M28.78 23.75L45.23 33.25" stroke="#B8E1F3" />
      <path d="M28.78 33.25V52.25M20.55 38L37 47.5" stroke="#EAF7FD" />
      <path d="M45.23 33.25V52.25M37 47.5L53.45 38" stroke="#68C1EA" />
    </g>
    <path
      d="M37 19L53.45 28.5V47.5L37 57L20.55 47.5V28.5ZM20.55 28.5L37 38L53.45 28.5M37 38V57"
      fill="none"
      strokeWidth="2.6"
      strokeLinejoin="round"
      style={line}
    />
    <g stroke="#FFFFFF" strokeLinecap="round">
      <path d="M24.2 34.5V41.5" strokeWidth="2.4" />
      <path d="M30.5 24.8L36 21.6" strokeWidth="2" />
    </g>
  </svg>
);
