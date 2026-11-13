import { ids, messages } from "./chat";
import { documentHeading, supplementHeading } from "../../src/answers";
import type { ChatMessage } from "../../src/chat";

export function answerFixture() {
  const doc = "\n🍚餐费上限150元[E1]。\n\n";
  const extra = "\n建议保留凭证。";
  const content = documentHeading + doc + supplementHeading + extra;
  const docStart = [...documentHeading].length;
  const docEnd = [...(documentHeading + doc)].length;
  const citationStart = [...(documentHeading + doc.split("[E1]")[0])].length;
  const message: ChatMessage = {
    ...messages[1]!,
    status: "completed",
    content,
  };
  const materials = {
    sections: [
      { kind: "document", start: docStart, end: docEnd },
      {
        kind: "supplement",
        start: docEnd + [...supplementHeading].length,
        end: [...content].length,
      },
    ],
    citations: [
      { citation_id: "E1", start: citationStart, end: citationStart + 4 },
    ],
    evidence: [
      {
        citation_id: "E1",
        knowledge_base_id: ids.user,
        document_id: ids.thread,
        version_id: ids.run,
        chunk_id: ids.assistant,
        section_id: ids.user,
        title_path: ["差旅制度", "餐费"],
        start: 10,
        end: 17,
        start_line: 2,
        end_line: 2,
        text: "餐费150元。",
        content_sha256: "a".repeat(64),
      },
    ],
  };
  return {
    message,
    response: {
      run_id: ids.run,
      message_id: ids.assistant,
      status: "succeeded",
      materials,
    },
  };
}
