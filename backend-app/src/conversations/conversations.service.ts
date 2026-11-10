import {
  BadRequestException,
  ConflictException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { DataSource, type EntityManager } from 'typeorm';
import { Conversation } from '../database/conversation.entity.js';
import { Attachment } from '../database/attachment.entity.js';
import {
  validateUploads,
  type ValidatedUpload,
} from '../attachments/validate-uploads.js';
import { Message } from '../database/message.entity.js';
import { OrchestratorService } from '../orchestrator/orchestrator.service.js';
import type {
  CreateConversationDto,
  ListConversationsDto,
  PaginationDto,
  RenameConversationDto,
  SendMessageDto,
} from './dto/chat.dto.js';

function paginated<T>(data: T[], totalItems: number, query: PaginationDto) {
  return {
    data,
    pagination: {
      page: query.page,
      pageSize: query.pageSize,
      totalItems,
      totalPages: Math.ceil(totalItems / query.pageSize),
    },
  };
}

@Injectable()
export class ConversationsService {
  constructor(
    private readonly db: DataSource,
    private readonly orchestrator: OrchestratorService,
  ) {}

  create(dto: CreateConversationDto) {
    const repo = this.db.getRepository(Conversation);
    return repo.save(repo.create({ title: dto.title ?? 'New chat' }));
  }

  async list(query: ListConversationsDto) {
    const builder = this.db
      .getRepository(Conversation)
      .createQueryBuilder('conversation');
    if (query.search)
      builder.where('conversation.title ILIKE :search', {
        search: `%${query.search.replace(/[\\%_]/g, '\\$&')}%`,
      });
    const [data, count] = await builder
      .orderBy('conversation.updatedAt', 'DESC')
      .addOrderBy('conversation.id', 'DESC')
      .skip((query.page - 1) * query.pageSize)
      .take(query.pageSize)
      .getManyAndCount();
    return paginated(data, count, query);
  }

  async get(id: string, manager = this.db.manager) {
    const conversation = await manager.findOneBy(Conversation, { id });
    if (!conversation) throw new NotFoundException('Conversation not found');
    return conversation;
  }

  async rename(id: string, dto: RenameConversationDto) {
    return this.db.transaction(async (manager) => {
      const conversation = await this.lock(manager, id);
      conversation.title = dto.title;
      conversation.version += 1;
      return manager.save(conversation);
    });
  }

  async remove(id: string) {
    await this.db.getRepository(Conversation).delete(id);
  }

  async messages(id: string, query: PaginationDto) {
    await this.get(id);
    const [data, count] = await this.db.getRepository(Message).findAndCount({
      where: { conversationId: id },
      order: { sequence: 'ASC', attachments: { position: 'ASC' } },
      skip: (query.page - 1) * query.pageSize,
      take: query.pageSize,
    });
    return paginated(data, count, query);
  }

  async download(id: string, attachmentId: string) {
    const attachment = await this.db
      .getRepository(Attachment)
      .createQueryBuilder('attachment')
      .innerJoin('attachment.message', 'message')
      .where(
        'attachment.id = :attachmentId AND message.conversation_id = :id',
        { attachmentId, id },
      )
      .addSelect('attachment.data')
      .getOne();
    if (!attachment) throw new NotFoundException('Attachment not found');
    return attachment;
  }

  async send(
    id: string,
    dto: SendMessageDto,
    files: Express.Multer.File[] = [],
  ) {
    const uploads = await validateUploads(files);
    if (!dto.content && !uploads.length)
      throw new BadRequestException(
        'Add a message or attach at least one file.',
      );
    const contentText = dto.content || 'Attached files';
    const conversation = await this.get(id);
    const existing = await this.replay(
      this.db.manager,
      conversation,
      dto,
      uploads,
    );
    if (existing) return existing;
    this.checkVersion(conversation, dto.expectedVersion);
    // PostgreSQL is the source of truth, including chats predating agent checkpoints.
    const history = await this.db.getRepository(Message).find({
      where: { conversationId: id },
      order: { sequence: 'ASC' },
      select: { role: true, content: true },
      loadEagerRelations: false,
    });
    const content = await this.orchestrator.reply(
      id,
      contentText,
      uploads.length,
      history.map(({ role, content }) => ({ role, content })),
    );
    return this.db.transaction(async (manager) => {
      const current = await this.lock(manager, id);
      const duplicate = await this.replay(manager, current, dto, uploads);
      if (duplicate) return duplicate;
      this.checkVersion(current, dto.expectedVersion);
      const last = await manager.findOne(Message, {
        where: { conversationId: id },
        order: { sequence: 'DESC' },
      });
      const next = (last?.sequence ?? -1) + 1;
      const messages = await manager.save(Message, [
        manager.create(Message, {
          conversationId: id,
          requestId: dto.requestId,
          role: 'user',
          content: contentText,
          sequence: next,
        }),
        manager.create(Message, {
          conversationId: id,
          requestId: dto.requestId,
          role: 'assistant',
          content,
          sequence: next + 1,
        }),
      ]);
      if (uploads.length)
        await manager.save(
          Attachment,
          uploads.map((upload) =>
            manager.create(Attachment, {
              ...upload,
              messageId: messages[0].id,
            }),
          ),
        );
      const savedMessages = await manager.find(Message, {
        where: { conversationId: id, requestId: dto.requestId },
        order: { sequence: 'ASC', attachments: { position: 'ASC' } },
      });
      if (current.version === 0 && current.title === 'New chat')
        current.title = (
          dto.content ||
          uploads[0]?.name ||
          'Attached files'
        ).slice(0, 60);
      current.version += 1;
      return {
        conversation: await manager.save(current),
        messages: savedMessages,
        mode: this.orchestrator.mode,
      };
    });
  }

  private async lock(manager: EntityManager, id: string) {
    const conversation = await manager.findOne(Conversation, {
      where: { id },
      lock: { mode: 'pessimistic_write' },
    });
    if (!conversation) throw new NotFoundException('Conversation not found');
    return conversation;
  }

  private checkVersion(conversation: Conversation, expected: number) {
    if (conversation.version !== expected)
      throw new ConflictException(
        'Conversation changed. Reload it before sending another message.',
      );
  }

  private async replay(
    manager: EntityManager,
    conversation: Conversation,
    dto: SendMessageDto,
    uploads: ValidatedUpload[],
  ) {
    const messages = await manager.find(Message, {
      where: { conversationId: conversation.id, requestId: dto.requestId },
      order: { sequence: 'ASC', attachments: { position: 'ASC' } },
    });
    if (!messages.length) return null;
    const stored = await manager.find(Attachment, {
      where: { messageId: messages[0].id },
      select: { id: true, name: true, sha256: true, position: true },
      order: { position: 'ASC' },
    });
    if (
      messages[0].content !== (dto.content || 'Attached files') ||
      stored.length !== uploads.length ||
      stored.some(
        (file, index) =>
          file.sha256 !== uploads[index].sha256 ||
          file.name !== uploads[index].name,
      )
    )
      throw new ConflictException(
        'requestId was already used for different content.',
      );
    return { conversation, messages, mode: this.orchestrator.mode };
  }
}
