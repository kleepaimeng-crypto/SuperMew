// @vitest-environment jsdom

import { describe, expect, it } from "vitest";
import { renderAnswerParts, renderMarkdown } from "./markdown";

describe("renderMarkdown", () => {
  it("渲染加粗、无序列表与标题", () => {
    const html = renderMarkdown(
      "**通用数据绘图 / 统计图**\n- **Origin**（OriginLab）：科研绘图软件。\n- **GraphPad Prism**：生物医学常用。",
    );

    expect(html).toContain("<strong>通用数据绘图 / 统计图</strong>");
    expect(html).toContain("<ul>");
    expect(html).toContain(
      "<li><strong>Origin</strong>（OriginLab）：科研绘图软件。</li>",
    );
    expect(html).toContain(
      "<li><strong>GraphPad Prism</strong>：生物医学常用。</li>",
    );
  });

  it("对常见语言代码块执行语法高亮，未知语言降级为 plaintext", () => {
    const pyHtml = renderMarkdown(
      "```python\ndef add(a: int, b: int) -> int:\n    return a + b\n```",
    );
    expect(pyHtml).toContain('class="hljs language-python"');
    expect(pyHtml).toContain("hljs-keyword");
    expect(pyHtml).toContain("hljs-title");

    const unknownHtml = renderMarkdown("```unknownlang\nhello <world>\n```");
    expect(unknownHtml).toContain('class="hljs language-plaintext"');
    expect(unknownHtml).toContain("hello &lt;world&gt;");
  });

  it("剥离原始 HTML、事件属性与普通模式下的 button 标签", () => {
    const html = renderMarkdown(
      '<img src=x onerror="alert(1)"><script>alert(2)</script><svg onload="alert(3)"></svg><button data-citation-id="E1">fake</button>',
    );

    expect(html).not.toContain("<img");
    expect(html).not.toContain("<script");
    expect(html).not.toContain("<svg");
    expect(html).not.toContain("<button");
    expect(html).not.toContain("onerror");
    expect(html).not.toContain("onload");
  });

  it.each([
    "[bad](javascript:alert(1))",
    "[bad](data:text/html,<script>alert(1)</script>)",
    "[bad](//attacker.example/phish)",
    "[bad](mailto:attacker@example.com)",
  ])("移除不安全的链接协议：%s", (md) => {
    const html = renderMarkdown(md);

    expect(html).not.toMatch(/href=/i);
    expect(html).not.toContain("javascript:");
    expect(html).not.toContain("data:text");
    expect(html).not.toContain("//attacker.example");
  });

  it("保留 http(s) 链接并加固新窗口属性", () => {
    const html = renderMarkdown("[Origin](https://www.originlab.com/)");

    expect(html).toContain('href="https://www.originlab.com/"');
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
  });
});

describe("renderAnswerParts", () => {
  it("在加粗与列表中保留已校验的引用按钮并反映展开状态", () => {
    const html = renderAnswerParts(
      [
        { text: "- **报销标准**：餐费上限 150 元 " },
        { text: "[E1]", citationId: "E1" },
        { text: "\n- 差旅住宿另算 " },
        { text: "[E2]", citationId: "E2" },
      ],
      "E1",
    );

    expect(html).toContain("<ul>");
    expect(html).toContain("<strong>报销标准</strong>");
    expect(html).toContain('data-citation-id="E1"');
    expect(html).toContain('aria-label="查看引用 E1"');
    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain('data-citation-id="E2"');
    expect(html).toContain('aria-expanded="false"');
  });

  it("不会把正文里的伪造 button 或脚本渲染成引用按钮", () => {
    const html = renderAnswerParts([
      { text: '正常内容 <button data-citation-id="E9">假按钮</button> ' },
      { text: "[E1]", citationId: "E1" },
    ]);

    expect(html).not.toContain('data-citation-id="E9"');
    expect(html).toContain('data-citation-id="E1"');
  });
});
