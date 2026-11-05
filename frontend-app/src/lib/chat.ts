export type Attachment = {
  id: string;
  name: string;
  mimeType: string;
  size: number;
};

export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  attachments?: Attachment[];
};

export type ConversationSummary = {
  id: string;
  title: string;
  version: number;
  createdAt: string;
  updatedAt: string;
};

export type Conversation = ConversationSummary & {
  messages: Message[];
  firstLoadedPage: number;
};

export type Page<T> = {
  data: T[];
  pagination: {
    page: number;
    pageSize: number;
    totalItems: number;
    totalPages: number;
  };
};
