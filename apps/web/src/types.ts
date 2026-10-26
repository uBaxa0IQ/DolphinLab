export type Member = { name: string; role: string; state: string; color: string }
export type Team = { id: string; name: string; focus: string; icon: 'layers' | 'sparkles' | 'shield'; state: string; stage: string; current: string; members: Member[]; updated: string; completed: number; total: number }
export type Experiment = { id: string; name: string; team: string; status: 'running' | 'verified' | 'rejected'; score: string; duration: string; detail: string }
export type FeedEvent = { id: string; time: string; title: string; detail: string; type: 'result' | 'team' | 'info' }
export type InputFile = { id: string; name: string; size: number; kind: string }
export type ChatMessage = { id: string; role: 'user' | 'assistant' | 'system'; content: string; time: string; directive?: boolean; status?: string }
export type Project = { id: string; name: string; description: string; status: string; demo?: boolean; metric?: string; score?: string; teams: Team[]; experiments: Experiment[]; events: FeedEvent[]; files: InputFile[] }
