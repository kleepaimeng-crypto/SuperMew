import DOMPurify from "dompurify";
import hljs from "highlight.js/lib/core";
import bash from "highlight.js/lib/languages/bash";
import cpp from "highlight.js/lib/languages/cpp";
import csharp from "highlight.js/lib/languages/csharp";
import css from "highlight.js/lib/languages/css";
import go from "highlight.js/lib/languages/go";
import java from "highlight.js/lib/languages/java";
import javascript from "highlight.js/lib/languages/javascript";
import json from "highlight.js/lib/languages/json";
import markdown from "highlight.js/lib/languages/markdown";
import plaintext from "highlight.js/lib/languages/plaintext";
import python from "highlight.js/lib/languages/python";
import rust from "highlight.js/lib/languages/rust";
import sql from "highlight.js/lib/languages/sql";
import typescript from "highlight.js/lib/languages/typescript";
import xml from "highlight.js/lib/languages/xml";
import yaml from "highlight.js/lib/languages/yaml";
import { Marked, type Tokens } from "marked";
import type { AnswerPart } from "./answers";

hljs.registerLanguage("bash", bash);
hljs.registerLanguage("cpp", cpp);
hljs.registerLanguage("csharp", csharp);
hljs.registerLanguage("css", css);
hljs.registerLanguage("go", go);
hljs.registerLanguage("java", java);
hljs.registerLanguage("javascript", javascript);
hljs.registerLanguage("json", json);
hljs.registerLanguage("markdown", markdown);
hljs.registerLanguage("plaintext", plaintext);
hljs.registerLanguage("python", python);
hljs.registerLanguage("rust", rust);
hljs.registerLanguage("sql", sql);
hljs.registerLanguage("typescript", typescript);
hljs.registerLanguage("xml", xml);
hljs.registerLanguage("yaml", yaml);

const CITATION_ID_RE = /^E[1-9][0-9]*$/;
const SAFE_HTTP_RE = /^https?:\/\//i;

const BASE_ALLOWED_TAGS = [
  "a",
  "blockquote",
  "br",
  "code",
  "del",
  "em",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "hr",
  "li",
  "ol",
  "p",
  "pre",
  "span",
  "strong",
  "sup",
  "table",
  "tbody",
  "td",
  "th",
  "thead",
  "tr",
  "ul",
];

const BASE_ALLOWED_ATTR = ["class", "href", "rel", "target", "title"];

const CITATION_ALLOWED_TAGS = [...BASE_ALLOWED_TAGS, "button"];
const CITATION_ALLOWED_ATTR = [
  ...BASE_ALLOWED_ATTR,
  "type",
  "data-citation-id",
  "aria-label",
  "aria-expanded",
];

const markdownParser = new Marked({
  gfm: true,
  breaks: true,
  renderer: {
    code({ text, lang }: Tokens.Code) {
      const requested =
        (lang ?? "").trim().split(/\s+/)[0]?.toLowerCase() ?? "";
      const validLanguage =
        requested && hljs.getLanguage(requested) ? requested : "plaintext";
      const highlighted = hljs.highlight(text, {
        language: validLanguage,
      }).value;
      return `<pre><code class="hljs language-${validLanguage}">${highlighted}</code></pre>`;
    },
    html() {
      return "";
    },
  },
});

function sanitizeMarkdownHtml(
  html: string,
  allowCitationButtons: boolean,
): string {
  const sanitized = DOMPurify.sanitize(html, {
    ALLOWED_TAGS: allowCitationButtons
      ? CITATION_ALLOWED_TAGS
      : BASE_ALLOWED_TAGS,
    ALLOWED_ATTR: allowCitationButtons
      ? CITATION_ALLOWED_ATTR
      : BASE_ALLOWED_ATTR,
    ALLOWED_URI_REGEXP: SAFE_HTTP_RE,
    ALLOW_UNKNOWN_PROTOCOLS: false,
    FORBID_ATTR: ["src", "srcset", "style"],
    FORBID_TAGS: [
      "embed",
      "form",
      "iframe",
      "img",
      "math",
      "object",
      "style",
      "svg",
    ],
    SANITIZE_NAMED_PROPS: true,
  });

  const template = document.createElement("template");
  template.innerHTML = sanitized;

  template.content.querySelectorAll("a").forEach((anchor) => {
    const href = anchor.getAttribute("href") ?? "";
    if (!SAFE_HTTP_RE.test(href)) {
      anchor.removeAttribute("href");
      anchor.removeAttribute("target");
      anchor.removeAttribute("rel");
      return;
    }
    anchor.setAttribute("target", "_blank");
    anchor.setAttribute("rel", "noopener noreferrer");
  });

  if (allowCitationButtons) {
    template.content.querySelectorAll("button").forEach((button) => {
      const id = button.getAttribute("data-citation-id") ?? "";
      if (!CITATION_ID_RE.test(id)) {
        button.remove();
        return;
      }
      button.setAttribute("type", "button");
      button.setAttribute("class", "citation-button");
      button.setAttribute("aria-label", `查看引用 ${id}`);
    });
  }

  return template.innerHTML;
}

export function renderMarkdown(text: string): string {
  const html = markdownParser.parse(text || "", { async: false }) as string;
  return sanitizeMarkdownHtml(html, false);
}

export function renderAnswerParts(
  parts: AnswerPart[],
  selectedCitationId: string | null = null,
): string {
  const plainText = parts.map((p) => p.text).join("");
  let prefix = "\uE000CITE";
  while (plainText.includes(prefix)) {
    prefix += "X";
  }
  const suffix = "\uE001";

  const citations: string[] = [];
  const assembled = parts
    .map((part) => {
      if (part.citationId && CITATION_ID_RE.test(part.citationId)) {
        const index = citations.length;
        citations.push(part.citationId);
        return `${prefix}${index}${suffix}`;
      }
      return part.text;
    })
    .join("");

  const parsed = markdownParser.parse(assembled, { async: false }) as string;
  const escapedPrefix = prefix.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const placeholderRe = new RegExp(`${escapedPrefix}(\\d+)${suffix}`, "g");

  const withCitations = parsed.replace(placeholderRe, (_match, rawIndex) => {
    const id = citations[Number(rawIndex)];
    if (!id) return "";
    const expanded = selectedCitationId === id ? "true" : "false";
    return `<button type="button" class="citation-button" data-citation-id="${id}" aria-label="查看引用 ${id}" aria-expanded="${expanded}">[${id}]</button>`;
  });

  return sanitizeMarkdownHtml(withCitations, true);
}
