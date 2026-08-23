import { useReducer, createContext, useContext } from "react";

export type AttachmentType = "image" | "pdf" | "code" | "markdown" | "file";

export interface Attachment {
  id: string; // The ID assigned by the backend
  file: File;
  type: AttachmentType;
  previewUrl?: string;
  metadata?: Record<string, unknown>;
  status: "uploading" | "ready" | "error";
  progress?: number;
}

export interface WorkspaceState {
  text: string;
  attachments: Attachment[];
  selectedModel: string;
  /**
   * "analyzing" was removed with the fake 1500ms pre-flight delay it existed to
   * display (see PlannerPreview). Narrowing the union rather than leaving the
   * member unused is what stops it being dispatched again.
   */
  preflightStatus: "idle" | "ready";
  isDragging: boolean;
}

export type WorkspaceAction =
  | { type: "SET_TEXT"; payload: string }
  | { type: "ADD_ATTACHMENTS"; payload: Attachment[] }
  | { type: "UPDATE_ATTACHMENT"; payload: { id: string; updates: Partial<Attachment> } }
  | { type: "REMOVE_ATTACHMENT"; payload: string }
  | { type: "SET_MODEL"; payload: string }
  | { type: "SET_PREFLIGHT_STATUS"; payload: WorkspaceState["preflightStatus"] }
  | { type: "SET_DRAGGING"; payload: boolean }
  | { type: "RESET" };

export const initialState: WorkspaceState = {
  text: "",
  attachments: [],
  selectedModel: "auto",
  preflightStatus: "idle",
  isDragging: false,
};

export function workspaceReducer(state: WorkspaceState, action: WorkspaceAction): WorkspaceState {
  switch (action.type) {
    case "SET_TEXT":
      return { ...state, text: action.payload };
    case "ADD_ATTACHMENTS":
      return { ...state, attachments: [...state.attachments, ...action.payload] };
    case "UPDATE_ATTACHMENT":
      return {
        ...state,
        attachments: state.attachments.map((att) =>
          att.id === action.payload.id ? { ...att, ...action.payload.updates } : att
        ),
      };
    case "REMOVE_ATTACHMENT":
      return {
        ...state,
        attachments: state.attachments.filter((att) => att.id !== action.payload),
      };
    case "SET_MODEL":
      return { ...state, selectedModel: action.payload };
    case "SET_PREFLIGHT_STATUS":
      return { ...state, preflightStatus: action.payload };
    case "SET_DRAGGING":
      return { ...state, isDragging: action.payload };
    case "RESET":
      return initialState;
    default:
      return state;
  }
}

export const WorkspaceContext = createContext<{
  state: WorkspaceState;
  dispatch: React.Dispatch<WorkspaceAction>;
} | null>(null);

export function useWorkspace() {
  const context = useContext(WorkspaceContext);
  if (!context) {
    throw new Error("useWorkspace must be used within a WorkspaceProvider");
  }
  return context;
}
