import type { SVGProps } from "react";

/** Faceted berg mark shared with the public docs site (`site/assets/svg/cryo-mark.svg`). */
export const CryoMark = ({ title, ...props }: SVGProps<SVGSVGElement> & { title?: string }) => (
  <svg viewBox="0 0 512 512" xmlns="http://www.w3.org/2000/svg" role={title ? "img" : "presentation"} {...props}>
    {title ? <title>{title}</title> : null}
    <g transform="translate(0 -10)">
      <g strokeWidth="1" strokeLinejoin="round">
        <path d="M92 206 L268 206 L186 300 L104 262 Z" fill="#3A8FD2" stroke="#3A8FD2" />
        <path d="M268 206 L262 340 L186 300 Z" fill="#277ABE" stroke="#277ABE" />
        <path d="M104 262 L186 300 L166 404 L142 330 Z" fill="#277ABE" stroke="#277ABE" />
        <path d="M186 300 L262 340 L248 500 L166 404 Z" fill="#1A579A" stroke="#1A579A" />
        <path d="M268 206 L420 206 L412 236 L380 292 L352 290 Z" fill="#57AEE1" stroke="#57AEE1" />
        <path d="M352 290 L380 292 L386 330 L330 410 Z" fill="#3A8FD2" stroke="#3A8FD2" />
        <path d="M268 206 L352 290 L262 340 Z" fill="#3A8FD2" stroke="#3A8FD2" />
        <path d="M352 290 L330 410 L248 500 L262 340 Z" fill="#277ABE" stroke="#277ABE" />
      </g>
      <g strokeWidth="1" strokeLinejoin="round">
        <path d="M262 36 L304 119 L332 166 L276 198 Z" fill="#E3F2FC" stroke="#E3F2FC" />
        <path d="M304 119 L318 104 L332 166 Z" fill="#B8E1F3" stroke="#B8E1F3" />
        <path d="M318 104 L392 198 L332 166 Z" fill="#E3F2FC" stroke="#E3F2FC" />
        <path d="M332 166 L392 198 L276 198 Z" fill="#B8E1F3" stroke="#B8E1F3" />
        <path d="M262 36 L276 198 L200 130 Z" fill="#8ECDEF" stroke="#8ECDEF" />
        <path d="M200 130 L276 198 L140 198 Z" fill="#68C1EA" stroke="#68C1EA" />
      </g>
      <path d="M64 202 Q256 188 448 202 Q256 216 64 202 Z" fill="#68C1EA" />
      <path
        transform="translate(285 212) rotate(22)"
        fill="var(--cryo-mark-core, #0B0F16)"
        d="M26 -54.1 A60 60 0 0 1 23 55.4 L20 205 A20 20 0 0 1 -20 205 L-23 55.4 A60 60 0 0 1 -26 -54.1 L-26 -11 Q-26 -3 -18 -3 L18 -3 Q26 -3 26 -11 Z"
      />
    </g>
  </svg>
);
