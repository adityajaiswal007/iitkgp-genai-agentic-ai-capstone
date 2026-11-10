import {
  Column,
  CreateDateColumn,
  Entity,
  JoinColumn,
  ManyToOne,
  PrimaryGeneratedColumn,
  Unique,
  type Relation,
} from 'typeorm';
import { Message } from './message.entity.js';

@Entity('attachments')
@Unique('attachments_message_position_key', ['messageId', 'position'])
export class Attachment {
  @PrimaryGeneratedColumn('uuid') id: string;
  @Column({ name: 'message_id', type: 'uuid' }) messageId: string;
  @ManyToOne(() => Message, { onDelete: 'CASCADE', nullable: false })
  @JoinColumn({ name: 'message_id' })
  message: Relation<Message>;
  @Column({ type: 'varchar', length: 180 }) name: string;
  @Column({ name: 'mime_type', type: 'varchar', length: 120 }) mimeType: string;
  @Column({ type: 'integer' }) size: number;
  @Column({ type: 'integer' }) position: number;
  @Column({ type: 'varchar', length: 64, select: false }) sha256: string;
  @Column({ type: 'bytea', select: false }) data: Buffer;
  @CreateDateColumn({ name: 'created_at', type: 'timestamptz' })
  createdAt: Date;
}
