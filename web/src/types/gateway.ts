export type AgentState = 'idle' | 'thinking' | 'executing' | 'perceiving' | 'error' | 'killed';

export interface ConfirmationRecord {
  id: string;
  action_type: string;
  details: Record<string, any>;
  risk_level: number;
  reason: string;
  timestamp: number;
}

export interface SharedFile {
  id: string;
  name: string;
  path?: string;
  mime: string;
  size: number;
  direction: 'bot' | 'user';
  note?: string;
  conversation_id?: string;
  created_at: number;
  missing?: boolean;
}

export interface ChatAction {
  tool: string;
  arguments?: string;
  result?: string;
  pending?: boolean;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  thoughts?: string[];
  actions?: ChatAction[];
  timestamp: number;
  streaming?: boolean;
  isHistorical?: boolean;
}

export interface ConversationItem {
  id: string;
  title: string;
  created_at?: number;
  updated_at?: number;
  message_count?: number;
  is_active?: boolean;
}

export interface InboundWSEvent {
  event: string;
  state?: AgentState;
  details?: any;
  killed?: boolean;
  chunk?: string;
  is_thought?: boolean;
  summary?: string;
  detail?: string;
  headline?: string;
  success?: boolean;
  thoughts?: string[];
  actions?: Array<{ tool?: string; arguments?: any; result?: any; pending?: boolean }>;
  confirmations?: ConfirmationRecord[];
  id?: string;
  action_type?: string;
  risk_level?: number;
  reason?: string;
  message?: string;
  tool?: string;
  arguments?: any;
  result?: any;
  goal?: string;
  time?: number;
  error?: string;
  conversation_id?: string;
  conversation_title?: string;
  title?: string;
  messages?: any[];
  conversations?: ConversationItem[];
  active_id?: string;
  file?: SharedFile;
  files?: SharedFile[];
  background_active?: boolean;
  active?: boolean;
  task?: string;
  primary?: PrimaryJobInfo | null;
  integrations?: Array<{ name: string }>;
  integration_focus?: string | null;
  running?: RunningJobInfo[];
}

export interface PrimaryJobInfo {
  job_id: string;
  name: string;
  phase?: string;
  status?: string;
  path?: string;
}

export interface RunningJobInfo {
  job_id: string;
  name: string;
  status?: string;
  completed_items?: number;
  total_items?: number;
}

export interface JobFocusState {
  primary: PrimaryJobInfo | null;
  integrations?: Array<{ name: string }>;
  integration_focus?: string | null;
  running?: RunningJobInfo[];
}

export interface BackgroundActivity {
  active: boolean;
  task: string;
  details: string;
}

export type ModelPreference = 'local' | 'auto' | 'cloud';
export type CloudProvider = 'chutes' | 'meta';

export interface ModelChoiceGroup {
  reasoning_model: string;
  vision_model: string;
  models: string[];
}

export interface ModelSettings {
  preference: ModelPreference;
  cloud_provider: CloudProvider;
  active_backend: 'local' | 'chutes' | 'meta';
  active_reasoning_model: string;
  active_vision_model: string;
  summary: string;
  persisted?: boolean;
  local: ModelChoiceGroup & { reachable: boolean | null };
  chutes: ModelChoiceGroup & { configured: boolean };
  meta: ModelChoiceGroup & {
    configured: boolean;
    reasoning_effort: string;
    efforts: string[];
  };
}

export interface OutboundWSMessage {
  type:
    | 'ping'
    | 'prompt'
    | 'stop'
    | 'cancel'
    | 'stop_task'
    | 'kill'
    | 'reset'
    | 'resolve_confirmation'
    | 'speak'
    | 'clear'
    | 'get_status'
    | 'get_confirmations'
    | 'new_chat'
    | 'switch_conversation'
    | 'get_conversations'
    | 'get_history'
    | 'sync'
    | 'edit_retry'
    | 'get_job_focus';
  prompt?: string;
  id?: string;
  message_index?: number;
  approved?: boolean;
  allowlist?: boolean;
  message?: string;
  voice?: string;
  title?: string;
  conversation_id?: string;
  search?: string;
}
