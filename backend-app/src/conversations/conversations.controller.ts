import {
  Body,
  Controller,
  Delete,
  Get,
  HttpCode,
  Header,
  Headers,
  Param,
  ParseUUIDPipe,
  Patch,
  Post,
  Query,
  StreamableFile,
  UploadedFiles,
  UseInterceptors,
} from '@nestjs/common';
import {
  ApiBody,
  ApiConsumes,
  ApiHeader,
  ApiOperation,
  ApiResponse,
  ApiTags,
} from '@nestjs/swagger';
import { FilesInterceptor } from '@nestjs/platform-express';
import { MAX_FILE_BYTES, MAX_FILES } from '../attachments/validate-uploads.js';
import { ChatRateLimit } from '../rate-limit/rate-limit.module.js';
import { UsageService } from '../usage/usage.service.js';
import { ConversationsService } from './conversations.service.js';
import {
  CreateConversationDto,
  ListConversationsDto,
  PaginationDto,
  RenameConversationDto,
  SendMessageDto,
} from './dto/chat.dto.js';

@ApiTags('conversations')
@ApiHeader({
  name: 'X-Client-ID',
  required: true,
  description: 'Persistent browser UUID v4',
  schema: { type: 'string', format: 'uuid' },
})
@ApiResponse({
  status: 429,
  description:
    'Browser quota exceeded. Retry-After specifies the cooldown in seconds.',
})
@Controller('conversations')
export class ConversationsController {
  constructor(
    private readonly conversations: ConversationsService,
    private readonly usage: UsageService,
  ) {}
  @Get('usage/daily')
  @Header('Cache-Control', 'private, no-store')
  @ApiOperation({ summary: 'Get daily token allowance for this browser' })
  daily(@Headers('x-client-id') clientId: string) {
    return this.usage.daily(clientId.toLowerCase());
  }
  @Post()
  @ApiOperation({ summary: 'Create a conversation' })
  create(@Body() body: CreateConversationDto) {
    return this.conversations.create(body);
  }
  @Get()
  @ApiOperation({ summary: 'List conversations, newest activity first' })
  list(@Query() query: ListConversationsDto) {
    return this.conversations.list(query);
  }
  @Get(':id')
  @ApiOperation({ summary: 'Get conversation metadata and version' })
  get(@Param('id', new ParseUUIDPipe({ version: '4' })) id: string) {
    return this.conversations.get(id);
  }
  @Patch(':id')
  @ApiOperation({ summary: 'Rename a conversation' })
  rename(
    @Param('id', new ParseUUIDPipe({ version: '4' })) id: string,
    @Body() body: RenameConversationDto,
  ) {
    return this.conversations.rename(id, body);
  }
  @Delete(':id')
  @HttpCode(204)
  @ApiOperation({
    summary: 'Delete a conversation and all its messages; idempotent',
  })
  remove(@Param('id', new ParseUUIDPipe({ version: '4' })) id: string) {
    return this.conversations.remove(id);
  }
  @Get(':id/messages')
  @ApiOperation({ summary: 'List messages in chronological sequence' })
  messages(
    @Param('id', new ParseUUIDPipe({ version: '4' })) id: string,
    @Query() query: PaginationDto,
  ) {
    return this.conversations.messages(id, query);
  }
  @Get(':id/attachments/:attachmentId')
  @Header('Cache-Control', 'private, no-store')
  @ApiOperation({ summary: 'Download an attachment from this conversation' })
  async download(
    @Param('id', new ParseUUIDPipe({ version: '4' })) id: string,
    @Param('attachmentId', new ParseUUIDPipe({ version: '4' }))
    attachmentId: string,
  ) {
    const attachment = await this.conversations.download(id, attachmentId);
    return new StreamableFile(Buffer.from(attachment.data), {
      type: 'application/octet-stream',
      disposition: `attachment; filename="download"; filename*=UTF-8''${encodeURIComponent(attachment.name).replace(/['()*]/g, (character) => `%${character.charCodeAt(0).toString(16).toUpperCase()}`)}`,
      length: attachment.size,
    });
  }
  @Post(':id/messages')
  @ApiConsumes('application/json', 'multipart/form-data')
  @ApiBody({
    schema: {
      type: 'object',
      required: ['content', 'requestId', 'expectedVersion'],
      properties: {
        content: {
          type: 'string',
          maxLength: 8000,
          description: 'May be empty when files are attached.',
        },
        requestId: { type: 'string', format: 'uuid' },
        expectedVersion: { type: 'integer', minimum: 0 },
        files: {
          type: 'array',
          maxItems: 5,
          items: { type: 'string', format: 'binary' },
          description: 'Multipart only. Up to 5 files, 5 MB each.',
        },
      },
    },
  })
  @UseInterceptors(
    FilesInterceptor('files', MAX_FILES, {
      limits: {
        fileSize: MAX_FILE_BYTES,
        files: MAX_FILES,
        fields: 3,
        fieldSize: 32768,
        fieldNameSize: 64,
        parts: 8,
      },
    }),
  )
  @ChatRateLimit()
  @ApiOperation({
    summary:
      'Execute an agent query and atomically store the user and assistant messages',
  })
  send(
    @Param('id', new ParseUUIDPipe({ version: '4' })) id: string,
    @Body() body: SendMessageDto,
    @Headers('x-client-id') clientId: string,
    @UploadedFiles() files: Express.Multer.File[] = [],
  ) {
    return this.conversations.send(id, body, files, clientId.toLowerCase());
  }
}
