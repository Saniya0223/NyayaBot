type Block =
  | { kind: 'heading'; text: string }
  | { kind: 'paragraph'; text: string }
  | { kind: 'list'; marker: string; text: string }
  | { kind: 'table'; headings: string[]; rows: string[][] };

const summarySections = new Set([
  'situation', 'legal issue', 'current status', 'relevant laws', 'documents', 'potential next steps',
]);

function plainText(value: string): string {
  return value.replace(/\*\*/g, '').replace(/\*([^*]+)\*/g, '$1').trim();
}

function tableCells(line: string): string[] {
  const value = line.trim().replace(/^\|/, '').replace(/\|$/, '');
  const cells: string[] = [];
  let cell = '';
  for (let index = 0; index < value.length; index += 1) {
    if (value[index] === '\\' && value[index + 1] === '|') {
      cell += '|';
      index += 1;
    } else if (value[index] === '|') {
      cells.push(plainText(cell));
      cell = '';
    } else {
      cell += value[index];
    }
  }
  cells.push(plainText(cell));
  return cells;
}

function isTableRule(line: string, columns: number): boolean {
  const cells = tableCells(line);
  return cells.length === columns && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function blocksFromText(text: string, summary: boolean): Block[] {
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  const blocks: Block[] = [];
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index].trim();
    if (!line) continue;

    if (line.includes('|') && index + 1 < lines.length) {
      const headings = tableCells(line);
      if (headings.length > 1 && isTableRule(lines[index + 1], headings.length)) {
        const rows: string[][] = [];
        index += 2;
        while (index < lines.length && lines[index].includes('|')) {
          const cells = tableCells(lines[index]);
          if (cells.length !== headings.length) break;
          rows.push(cells);
          index += 1;
        }
        blocks.push({ kind: 'table', headings, rows });
        index -= 1;
        continue;
      }
    }

    const markdownHeading = /^#{1,6}\s+(.+)$/.exec(line);
    if (markdownHeading) {
      blocks.push({ kind: 'heading', text: plainText(markdownHeading[1]) });
      continue;
    }
    const cleaned = plainText(line);
    const summaryHeading = summary ? /^([^:]+?)(?::\s*(.*))?$/.exec(cleaned) : null;
    if (summaryHeading && summarySections.has(summaryHeading[1].trim().toLowerCase())) {
      blocks.push({ kind: 'heading', text: summaryHeading[1].trim() });
      if (summaryHeading[2]) blocks.push({ kind: 'paragraph', text: summaryHeading[2] });
      continue;
    }
    const bullet = /^([-*•])\s+(.+)$/.exec(line);
    const numbered = /^(\d+[.)])\s+(.+)$/.exec(line);
    if (bullet || numbered) {
      blocks.push({ kind: 'list', marker: numbered ? numbered[1] : '•', text: plainText((numbered || bullet)![2]) });
      continue;
    }
    blocks.push({ kind: 'paragraph', text: cleaned });
  }
  return blocks;
}

export default function FormattedMessage({ text, variant = 'chat' }: { text: string; variant?: 'chat' | 'summary' }) {
  const summary = variant === 'summary';
  return (
    <div className={summary ? 'space-y-3 text-sm leading-6 text-[#383530]' : 'space-y-2.5'}>
      {blocksFromText(text, summary).map((block, index) => {
        if (block.kind === 'heading') {
          return <h3 key={index} className={summary ? 'border-t border-[#eee8dd] pt-3 text-sm font-bold text-[#171717] first:border-0 first:pt-0' : 'pt-1 text-xs font-bold uppercase tracking-[0.08em] text-[#181818]'}>{block.text}</h3>;
        }
        if (block.kind === 'list') {
          return <p key={index} className="flex gap-2"><span className="shrink-0 font-semibold text-[#9d6b00]">{block.marker}</span><span className="min-w-0 break-words">{block.text}</span></p>;
        }
        if (block.kind === 'table') {
          return (
            <div key={index} className="soft-scrollbar max-w-full overflow-x-auto rounded-xl border border-[#e8e1d4]" role="region" aria-label="Response table" tabIndex={0}>
              <table className="w-full min-w-[360px] border-collapse text-left text-xs leading-5">
                <thead className="bg-[#fff6dc] text-[#29251d]"><tr>{block.headings.map((heading, column) => <th key={column} scope="col" className="border-b border-[#e8e1d4] px-3 py-2 font-bold">{heading}</th>)}</tr></thead>
                <tbody>{block.rows.map((row, rowIndex) => <tr key={rowIndex} className="even:bg-[#fdfbf6]">{row.map((cell, column) => <td key={column} className="border-t border-[#f0ece4] px-3 py-2 align-top break-words">{cell}</td>)}</tr>)}</tbody>
              </table>
            </div>
          );
        }
        return <p key={index} className="break-words">{block.text}</p>;
      })}
    </div>
  );
}
