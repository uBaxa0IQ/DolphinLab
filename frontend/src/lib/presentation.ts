/** Display only: original evidence and conversation text remain unchanged. */
export function readableText(original: string): string {
  let technicalSection = false;
  let inCode = false;
  const lines: string[] = [];
  const input = original.replace(
    /```(?:json)?\s*\n([\s\S]*?)```/g,
    (block, body: string) =>
      /["'](?:result_id|sha256|action_id|checks)["']\s*:/.test(body)
        ? ""
        : block,
  );
  for (const line of input.split("\n")) {
    if (/^\s*```/.test(line)) {
      inCode = !inCode;
      lines.push(line);
      continue;
    }
    const heading = line.match(
      /^\s*(?:#{1,6}\s+|\*\*)([^*\n]+?)(?:\*\*:?\s*)?$/,
    );
    if (heading)
      technicalSection =
        /^(создано|точный источник|document_sources|prior_results|идентификаторы|технические)/i.test(
          heading[1],
        );
    if (technicalSection) continue;
    if (
      /^\s*[-*>\s]*(?:`?(?:version_id|document_id|chunk_id|input_id|action_id|sha256|goal revision|iteration|run_started)`?\s*[:=]|SHA256 (?:фрагмента|оригинала))/i.test(
        line,
      )
    )
      continue;
    if (inCode) {
      lines.push(line);
      continue;
    }
    lines.push(
      line
        .replace(
          /\([^)]*(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f-]{20,}|SHA256|(?:input|chunk|version|action)_id)[^)]*\)/gi,
          "",
        )
        .replace(
          /\b(?:version_id|document_id|chunk_id|input_id|action_id|SHA256|chunk SHA256)\s*[:=]?\s*`?[a-z0-9_.…-]+`?[,;]?/gi,
          "",
        )
        .replace(
          /`?\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b`?/gi,
          "",
        )
        .replace(/`?\b[0-9a-f]{32,64}\b`?/gi, "")
        .replace(
          /`?\b(?:result|job|assessment|assignment|tech_lead|evaluator|data_analyst|page)_[a-z0-9]{8,}\b`?/gi,
          "",
        )
        .replace(/\(\s*[,;·/\s]*\)/g, "")
        .replace(/\bevaluator\b/gi, "независимый проверяющий")
        .replace(/независимый\s+независимый/gi, "независимый")
        .replace(/\bprepared\b/gi, "подготовлена")
        .replace(/\banalysis\b/gi, "анализ данных")
        .replace(/Definitions/gi, "Определения")
        .replace(/[ \t]{2,}/g, " ")
        .trimEnd(),
    );
  }
  return lines
    .join("\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function shortText(text: string, length = 180): string {
  const value = readableText(text)
    .replace(/[*`#]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return value.length > length
    ? value.slice(0, length).replace(/\s+\S*$/, "") + "…"
    : value;
}
