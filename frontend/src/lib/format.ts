export function size(bytes: number) {
  return bytes > 1048576
    ? `${(bytes / 1048576).toFixed(1)} МБ`
    : `${Math.max(1, Math.round(bytes / 1024))} КБ`;
}
