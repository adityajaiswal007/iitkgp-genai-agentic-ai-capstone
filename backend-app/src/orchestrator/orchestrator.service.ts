import { Injectable } from '@nestjs/common';

export type OrchestrationMessage = {
  role: 'user' | 'assistant';
  content: string;
};

@Injectable()
export class OrchestratorService {
  readonly mode = 'demo' as const;

  // Provider boundary: replace this with hosted <=8B agent orchestration.
  // Keep provider credentials here on the server; do not place them in the frontend.
  async reply(
    history: OrchestrationMessage[],
    content: string,
    attachmentCount = 0,
  ): Promise<string> {
    void history;
    if (attachmentCount)
      return `Your ${attachmentCount === 1 ? 'attachment has' : 'attachments have'} been saved with this message. You can download ${attachmentCount === 1 ? 'it' : 'them'} from the conversation.\n\nThis is a demo reply. Document and image analysis is not connected yet, so I have not read or analysed the uploaded files.`;
    if (/compare|review|vendor/i.test(content)) {
      return 'A useful review starts with a consistent set of criteria:\n\n1. Define the requirements and decision owner.\n2. Collect comparable evidence for each option.\n3. Compare cost, capabilities, risks, and missing information.\n4. Have a person review the evidence before making a decision.\n\nThis is a sample response from the backend orchestrator. Connect hosted inference and document retrieval to review actual evidence.';
    }
    return 'Let’s break the task into a clear plan:\n\n1. Define the outcome and the information you need.\n2. Gather relevant documents and check their dates.\n3. Work through the question using supporting evidence.\n4. Review the result and decide on the next step.\n\nThis is a sample response from the backend orchestrator. This conversation is stored in PostgreSQL; no language model or document corpus is connected yet.';
  }
}
