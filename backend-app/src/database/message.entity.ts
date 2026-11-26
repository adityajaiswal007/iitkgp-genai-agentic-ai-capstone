import {
  Column,
  CreateDateColumn,
  Entity,
  JoinColumn,
  ManyToOne,
  OneToMany,
  PrimaryGeneratedColumn,
  Unique,
} from 'typeorm';
import { Attachment } from './attachment.entity.js';
import { Conversation } from './conversation.entity.js';

@Entity('messages')
@Unique('messages_conversation_sequence_key', ['conversationId', 'sequence'])
@Unique('messages_request_role_key', ['conversationId', 'requestId', 'role'])
export class Message {
  @PrimaryGeneratedColumn('uuid') id: string;
  @Column({ name: 'conversation_id', type: 'uuid' }) conversationId: string;
  @ManyToOne(() => Conversation, { onDelete: 'CASCADE' })
  @JoinColumn({ name: 'conversation_id' })
  conversation: Conversation;
  @Column({ name: 'request_id', type: 'uuid' }) requestId: string;
  @Column({ type: 'varchar', length: 16 }) role: 'user' | 'assistant';
  @Column({ type: 'text' }) content: string;
  @OneToMany(() => Attachment, (attachment) => attachment.message, {
    eager: true,
  })
  attachments: Attachment[];
  @Column({ type: 'integer' }) sequence: number;
  @CreateDateColumn({ name: 'created_at', type: 'timestamptz' })
  createdAt: Date;
}
