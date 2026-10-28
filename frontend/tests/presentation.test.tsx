import { PreparedTable } from "../src/features/research/ReportPanel";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import Markdown from "../src/components/Markdown";
import { readableText } from "../src/lib/presentation";

const id = "f9bfd521-06be-44a9-8f5f-0258d978c98f";
const original = `Готово — следующая итерация подготовлена.

**Создано:**
- Goal revision: \`${id}\`
- action_id: \`${id}\`

**Суть задачи:**
Сравнить **август 2026** и **сентябрь 2026**: 160 000 / 170 000 RUB, +6,25%.

Выручка без НДС = amount - vat_amount. Значение неизвестно без vat_amount.
`;
const readable = readableText(original);
assert(!readable.includes(id));
for (const fact of [
  "160 000",
  "170 000",
  "+6,25%",
  "RUB",
  "amount - vat_amount",
  "неизвестно без vat_amount",
])
  assert(readable.includes(fact), fact);
const html = renderToStaticMarkup(
  createElement(Markdown, { text: original, technical: true }),
);
assert(html.includes("<strong>август 2026</strong>"));
assert(html.includes("<details"));
assert(!html.split("<details")[0].includes(id));
assert(html.includes(id), "Original evidence must remain available");
const hostile = renderToStaticMarkup(
  createElement(Markdown, {
    text: "<script>alert(1)</script>\n\n[link](javascript:alert%281%29)\n\n![remote](https://example.invalid/tracker.png)",
  }),
);
assert(!hostile.includes("<script"));
assert(!hostile.includes('href="javascript:'));
assert(!hostile.includes("<img"));
const table = renderToStaticMarkup(
  createElement(Markdown, {
    text: "| Месяц | Сумма |\n|---|---|\n| Сентябрь | 170000 |",
  }),
);
assert(table.includes("<table>"));
assert(table.includes("170000"));
assert(
  !readableText(
    '```json\n{"result_id":"result_1234567890","sha256":"abc"}\n```',
  ).includes("result_id"),
);
console.log("Markdown, evidence preservation and unsafe-content checks passed");

const provenance = renderToStaticMarkup(
  createElement(PreparedTable, {
    data: {
      columns: [
        { name: "item", label: "Проверка", type: "string", unit: "" },
        { name: "value", label: "Значение", type: "string", unit: "" },
        {
          name: "source",
          label: "Источник (version_id / SHA256)",
          type: "string",
          unit: "",
        },
      ],
      rows: [
        ["Колонка vat_amount", "отсутствует", id],
        ["SHA256 sales.csv", "a".repeat(64), id],
      ],
      selection: "Схема CSV",
      sample_size: 4,
    },
  }),
);
assert(!provenance.split("Технические атрибуты источников")[0].includes(id));
assert(provenance.includes("отсутствует"));
assert(
  provenance.includes("a".repeat(64)),
  "Source metadata remains available on demand",
);
