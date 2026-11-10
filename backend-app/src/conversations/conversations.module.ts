import { Module } from '@nestjs/common';
import { OrchestratorModule } from '../orchestrator/orchestrator.module.js';
import { ConversationsController } from './conversations.controller.js';
import { ConversationsService } from './conversations.service.js';
@Module({
  imports: [OrchestratorModule],
  controllers: [ConversationsController],
  providers: [ConversationsService],
})
export class ConversationsModule {}
