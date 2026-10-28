export const isDocument = (name: string) => /\.(pdf|docx|md|txt)$/i.test(name) && name.toLowerCase() !== "task.md";
