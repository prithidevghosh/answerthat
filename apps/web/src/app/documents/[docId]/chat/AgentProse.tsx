'use client';

import { Fragment, type ReactNode } from 'react';

/**
 * The agent's message, typeset.
 *
 * The model writes Markdown — headings, lists, `ids` in backticks, links — and
 * this screen was rendering it as `whitespace-pre-wrap` plain text, so a reply
 * listing two dozen findings arrived as a wall of literal `###`, backticks and
 * hyphens. That is not a small cosmetic problem on this product: the agent's
 * prose *is* the body text of the page (§5), and body text showing its own
 * markup reads as a rendering fault, exactly like the frosted composer §4
 * forbids.
 *
 * **Built as React elements, never as an HTML string.** There is no
 * `dangerouslySetInnerHTML` here and there must never be: this text comes from a
 * model, by way of a document a stranger uploaded, and the one thing a
 * transcript of someone else's paper must not do is execute it.
 *
 * Deliberately small, and deliberately not a library. It covers what the
 * orchestrator's prompt actually produces — headings, bullet and numbered
 * lists, bold, italic, inline code, links, block quotes, rules — and renders
 * anything it does not recognise verbatim, which is the honest failure mode for
 * a transcript. A partially-streamed line (`**bo`) simply does not match and
 * shows as itself until the final `message` replaces it.
 *
 * Every size and colour below is a token. Nothing here introduces type the rest
 * of the product does not already use: display for the two heading levels a
 * message can carry, the engraved label for the small ones, mono for ids and
 * DOIs because they are evidence and must look like evidence (§3).
 */

export function AgentProse({ text }: { text: string }) {
  return <div className="measure space-y-4">{renderBlocks(text)}</div>;
}

// --- blocks ---

type Block =
  | { kind: 'p'; lines: string[] }
  | { kind: 'h'; level: number; text: string }
  | { kind: 'list'; ordered: boolean; items: string[] }
  | { kind: 'quote'; lines: string[] }
  | { kind: 'code'; lines: string[] }
  | { kind: 'rule' };

const HEADING = /^(#{1,6})\s+(.*)$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const ORDERED = /^\s*\d+[.)]\s+(.*)$/;
const QUOTE = /^\s*>\s?(.*)$/;
const RULE = /^\s*(?:---+|\*\*\*+|___+)\s*$/;
const FENCE = /^\s*```/;

function parse(text: string): Block[] {
  const lines = text.split('\n');
  const blocks: Block[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (line.trim() === '') {
      i++;
      continue;
    }

    if (FENCE.test(line)) {
      const body: string[] = [];
      i++;
      while (i < lines.length && !FENCE.test(lines[i])) body.push(lines[i++]);
      i++; // closing fence, or the end of a stream that has not sent one yet
      blocks.push({ kind: 'code', lines: body });
      continue;
    }

    if (RULE.test(line)) {
      blocks.push({ kind: 'rule' });
      i++;
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({ kind: 'h', level: heading[1].length, text: heading[2] });
      i++;
      continue;
    }

    if (QUOTE.test(line)) {
      const body: string[] = [];
      while (i < lines.length && QUOTE.test(lines[i])) {
        body.push(QUOTE.exec(lines[i])![1]);
        i++;
      }
      blocks.push({ kind: 'quote', lines: body });
      continue;
    }

    if (BULLET.test(line) || ORDERED.test(line)) {
      const ordered = !BULLET.test(line) && ORDERED.test(line);
      const items: string[] = [];
      while (i < lines.length) {
        const item = ordered ? ORDERED.exec(lines[i]) : BULLET.exec(lines[i]);
        if (!item) {
          // A wrapped continuation line belongs to the item above it.
          if (items.length > 0 && /^\s+\S/.test(lines[i]) && lines[i].trim() !== '') {
            items[items.length - 1] += ` ${lines[i].trim()}`;
            i++;
            continue;
          }
          break;
        }
        items.push(item[1]);
        i++;
      }
      blocks.push({ kind: 'list', ordered, items });
      continue;
    }

    const body: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !HEADING.test(lines[i]) &&
      !BULLET.test(lines[i]) &&
      !ORDERED.test(lines[i]) &&
      !QUOTE.test(lines[i]) &&
      !RULE.test(lines[i]) &&
      !FENCE.test(lines[i])
    ) {
      body.push(lines[i++]);
    }
    blocks.push({ kind: 'p', lines: body });
  }

  return blocks;
}

function renderBlocks(text: string): ReactNode {
  return parse(text).map((block, i) => {
    switch (block.kind) {
      case 'rule':
        // §4: rules as structure. The double rule, not a grey bar.
        return (
          <span key={i} aria-hidden="true" className="block py-1">
            <span className="block h-px bg-[var(--rule-fine)]" />
            <span className="mt-[3px] block h-px bg-[var(--rule-hair)]" />
          </span>
        );

      case 'h':
        // A heading inside a message is structure within one answer, not a page
        // heading — so the small levels take the engraved label rather than
        // display type, which §3 will not set below 20px anyway.
        return block.level <= 2 ? (
          <p key={i} className="pt-2 font-display text-lg leading-tight text-primary">
            {inline(block.text)}
          </p>
        ) : (
          <p key={i} className="engraved-label pt-2 text-muted">
            {inline(block.text)}
          </p>
        );

      case 'list':
        return block.ordered ? (
          <ol key={i} className="space-y-1.5 pl-1">
            {block.items.map((item, n) => (
              <li key={n} className="flex gap-3">
                <span aria-hidden="true" className="shrink-0 font-mono text-2xs text-muted">
                  {n + 1}.
                </span>
                <span className="min-w-0 font-body text-base leading-[1.68] text-primary">
                  {inline(item)}
                </span>
              </li>
            ))}
          </ol>
        ) : (
          <ul key={i} className="space-y-1.5 pl-1">
            {block.items.map((item, n) => (
              <li key={n} className="flex gap-3">
                {/* A hairline dash, the engraver's list mark. Not a bullet glyph. */}
                <span
                  aria-hidden="true"
                  className="mt-[0.7em] h-px w-2 shrink-0 bg-[var(--rule-strong)]"
                />
                <span className="min-w-0 font-body text-base leading-[1.68] text-primary">
                  {inline(item)}
                </span>
              </li>
            ))}
          </ul>
        );

      case 'quote':
        return (
          <blockquote
            key={i}
            className="border-l-2 border-[var(--rule-hair)] pl-4 font-body text-base italic leading-[1.68] text-secondary"
          >
            {inline(block.lines.join(' '))}
          </blockquote>
        );

      case 'code':
        return (
          <pre
            key={i}
            className="overflow-x-auto whitespace-pre-wrap break-words border border-hair bg-paper-deep px-4 py-3 font-mono text-2xs leading-relaxed text-secondary"
          >
            {block.lines.join('\n')}
          </pre>
        );

      case 'p':
        return (
          <p key={i} className="font-body text-base leading-[1.68] text-primary">
            {inline(block.lines.join('\n'))}
          </p>
        );
    }
  });
}

// --- inline ---

/**
 * Code first, so markup inside backticks stays literal, then links, then
 * emphasis. A bare URL is linked too — the agent quotes DOIs constantly and an
 * unclickable one is a worse citation than none.
 */
const INLINE =
  /(`[^`]+`)|(\[[^\]]*\]\([^)\s]+\))|(\*\*[^*]+\*\*)|(__[^_]+__)|(\*[^*\n]+\*)|(_[^_\n]+_)|(https?:\/\/[^\s<>()]+)/g;

/** A letter, digit or underscore — the boundary that decides intra-word `_`. */
const isWordChar = (ch: string | undefined) => ch !== undefined && /\w/.test(ch);

function inline(text: string): ReactNode {
  const out: ReactNode[] = [];
  let last = 0;
  let key = 0;

  for (const match of text.matchAll(INLINE)) {
    const at = match.index;
    if (at > last) out.push(<Fragment key={key++}>{text.slice(last, at)}</Fragment>);
    const token = match[0];

    // `_` does not open emphasis inside a word — `unverifiable_no_abstract` is
    // one identifier, not "unverifiable", *no*, "abstract". GitHub-flavoured
    // Markdown draws the same line, and the model writes these constantly:
    // verification labels, span ids, tool names. Checked against the
    // surrounding characters rather than with a lookbehind, because a
    // lookbehind throws at parse time on older Safari and would take the whole
    // transcript down with it.
    if ((token.startsWith('_') || token.startsWith('__')) && !token.startsWith('*')) {
      const before = text[at - 1];
      const after = text[at + token.length];
      if (isWordChar(before) || isWordChar(after)) {
        out.push(<Fragment key={key++}>{token}</Fragment>);
        last = at + token.length;
        continue;
      }
    }

    if (token.startsWith('`')) {
      // Ids, DOIs and raw strings are evidence; they must look like evidence —
      // and `normal-case` is load-bearing, not tidying. Inside an engraved-label
      // heading the code span inherits `text-transform: uppercase`, which
      // rendered `fnd_924000c26b58d06f` as `FND_924000C26B58D06F`. That is a
      // different string. An id the user cannot copy is worse than no id.
      out.push(
        <code key={key++} className="break-all font-mono text-xs normal-case text-primary">
          {token.slice(1, -1)}
        </code>,
      );
    } else if (token.startsWith('[')) {
      const split = token.indexOf('](');
      out.push(
        <Link key={key++} href={token.slice(split + 2, -1)}>
          {inline(token.slice(1, split))}
        </Link>,
      );
    } else if (token.startsWith('**') || token.startsWith('__')) {
      out.push(
        <strong key={key++} className="font-medium text-primary">
          {inline(token.slice(2, -2))}
        </strong>,
      );
    } else if (token.startsWith('*') || token.startsWith('_')) {
      out.push(<em key={key++}>{inline(token.slice(1, -1))}</em>);
    } else {
      out.push(
        <Link key={key++} href={token}>
          {token}
        </Link>,
      );
    }

    last = at + token.length;
  }

  if (last < text.length) out.push(<Fragment key={key++}>{text.slice(last)}</Fragment>);
  return out;
}

/**
 * Every link the agent writes leaves the product, so every one of them says so
 * and carries `noreferrer`. `href` is rendered as an attribute and never
 * executed; anything that is not http(s) is shown as text rather than linked,
 * because `javascript:` in a transcript is not a citation.
 */
function Link({ href, children }: { href: string; children: ReactNode }) {
  if (!/^https?:\/\//i.test(href)) return <>{children}</>;
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      className="break-words text-cobalt underline decoration-cobalt/30 underline-offset-2 hover:decoration-cobalt"
    >
      {children}
    </a>
  );
}
