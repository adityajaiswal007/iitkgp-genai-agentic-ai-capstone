import { ApiProperty, ApiPropertyOptional } from '@nestjs/swagger';
import { Transform, Type } from 'class-transformer';
import {
  IsInt,
  IsString,
  IsUUID,
  Length,
  Max,
  Min,
  ValidateIf,
} from 'class-validator';
const trim = ({ value }: { value: unknown }) =>
  typeof value === 'string' ? value.trim() : value;

export class CreateConversationDto {
  @ApiPropertyOptional({ maxLength: 120, example: 'Vendor review' })
  @ValidateIf((_object, value) => value !== undefined)
  @Transform(trim)
  @IsString()
  @Length(1, 120)
  title?: string;
}
export class RenameConversationDto {
  @ApiProperty({ maxLength: 120, example: 'Q4 vendor review' })
  @Transform(trim)
  @IsString()
  @Length(1, 120)
  title: string;
}
export class SendMessageDto {
  @ApiProperty({ maxLength: 8000, example: 'Help me plan a vendor review.' })
  @Transform(trim)
  @IsString()
  @Length(0, 8000)
  content: string;
  @ApiProperty({
    format: 'uuid',
    description:
      'A fresh UUID per turn. Reuse it only when retrying the same content.',
  })
  @IsUUID('4')
  requestId: string;
  @ApiProperty({
    minimum: 0,
    description: 'Conversation version returned by the previous read or write.',
  })
  @Transform(({ value }: { value: unknown }) =>
    typeof value === 'string' && /^\d+$/.test(value) ? Number(value) : value,
  )
  @IsInt()
  @Min(0)
  expectedVersion: number;
}
export class PaginationDto {
  @ApiPropertyOptional({ default: 1, minimum: 1 })
  @Type(() => Number)
  @IsInt()
  @Min(1)
  page = 1;
  @ApiPropertyOptional({ default: 20, minimum: 1, maximum: 100 })
  @Type(() => Number)
  @IsInt()
  @Min(1)
  @Max(100)
  pageSize = 20;
}
export class ListConversationsDto extends PaginationDto {
  @ApiPropertyOptional({ maxLength: 200 })
  @ValidateIf((_object, value) => value !== undefined)
  @Transform(trim)
  @IsString()
  @Length(0, 200)
  search?: string;
}
