# Claude Code transcripts

`compact.jsonl` is synthetic (no `parentUuid`, so it covers the file-order fallback). It follows the shape of a Claude Code session
transcript (JSON lines under `~/.claude/projects/`) after a compaction: a
`compact_boundary` system entry, then a user entry with
`"isCompactSummary": true` whose `message.content` is the summary.

`compact-2.1.302.jsonl` is a recorded shape: the two lines Claude Code
2.1.302 wrote on a Mac for a manual `/compact` (2026-10-18), verbatim
except that text values are replaced (the boundary's `content` with
"Conversation compacted", the summary's `message.content` with a fake
summary). The summary's `parentUuid` is the boundary's `uuid`, its `uuid` is
`compactMetadata.preservedSegment.anchorUuid`, and its timestamp is earlier
than the boundary's although it comes after it in the file.
