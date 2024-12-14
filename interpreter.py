import re
from datetime import datetime
from typing import Dict, List
import json
import os

class BlogInterpreter:
    def __init__(self, template_path: str):
        with open(template_path, 'r', encoding='utf-8') as f:
            self.template = f.read()
        
        # Ajuste no padrão regex para o título
        self.markers = {
            'title': r'^\#\s+(.+)$',  # Padrão atualizado para título
            'metadata': r'^@(\w+):\s*(.*?)$',
            'heading': r'^##\s+(.+)$',
            'code': r'```(\w+)?\n(.*?)```',
            'paragraph': r'^(?![@#]|```)(.*?)$'
        }
    
    def get_sanitized_filename(self, title: str) -> str:
        filename = re.sub(r'[^\w\s-]', '', title)
        filename = re.sub(r'[-\s]+', '-', filename).strip('-')
        return filename.lower() + '.html'

    def process_text(self, content: str, output_dir: str = '.'):
        content = content.lstrip('\ufeff\n\r\t ')
        
        # Verifica se o conteúdo começa com #
        if not content.strip().startswith('#'):
            raise ValueError("O texto deve começar com um título (# Título)")
        
        blog_data = self.parse_text(content)
        
        if not blog_data['title']:
            raise ValueError("Não foi possível extrair o título do texto")
            
        filename = self.get_sanitized_filename(blog_data['title'])
        output_path = os.path.join(output_dir, filename)
        
        html = self.generate_html(blog_data)
        
        os.makedirs(output_dir, exist_ok=True)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html)
            
        return output_path

    def parse_text(self, content: str) -> Dict:
        lines = content.strip().split('\n')
        blog_data = {
            'title': '',
            'metadata': {},
            'sections': []
        }
        
        current_section = None
        code_block = None
        
        # Processa o título primeiro
        for i, line in enumerate(lines):
            if re.match(self.markers['title'], line):
                blog_data['title'] = re.match(self.markers['title'], line).group(1)
                lines = lines[i+1:]  # Remove a linha do título do processamento
                break
        
        for line in lines:
            # Processa metadados
            meta_match = re.match(self.markers['metadata'], line)
            if meta_match:
                key, value = meta_match.groups()
                blog_data['metadata'][key] = value
                continue
            
            # Processa cabeçalhos de seção
            heading_match = re.match(self.markers['heading'], line)
            if heading_match:
                if current_section:
                    blog_data['sections'].append(current_section)
                current_section = {
                    'title': heading_match.group(1),
                    'content': [],
                    'code_examples': []
                }
                continue
            
            # Processa blocos de código
            if line.startswith('```'):
                if code_block is None:
                    code_block = []
                    continue
                else:
                    if current_section:
                        current_section['code_examples'].append('\n'.join(code_block))
                    code_block = None
                    continue
            
            if code_block is not None:
                code_block.append(line)
                continue
            
            # Processa parágrafos
            if line.strip() and current_section:
                current_section['content'].append(line)
        
        # Adiciona última seção
        if current_section:
            blog_data['sections'].append(current_section)
        
        return blog_data

    def generate_html(self, blog_data: Dict) -> str:
        html = self.template.replace(
            '<h1>A Arte do Café e do Código Limpo</h1>',
            f'<h1>{blog_data["title"]}</h1>'
        )
        
        meta_html = ''
        for key, value in blog_data['metadata'].items():
            meta_html += f'<span>{value}</span>'
        
        html = re.sub(
            r'<div class="blog-meta">.*?</div>',
            f'<div class="blog-meta">{meta_html}</div>',
            html,
            flags=re.DOTALL
        )
        
        toc_html = ''
        for i, section in enumerate(blog_data['sections']):
            section_id = f"section-{i+1}"
            toc_html += f'<a href="#{section_id}">{section["title"]}</a>\n'
        
        html = re.sub(
            r'<nav>\s*<a.*?</nav>',
            f'<nav>{toc_html}</nav>',
            html,
            flags=re.DOTALL
        )
        
        content_html = ''
        for i, section in enumerate(blog_data['sections']):
            section_id = f"section-{i+1}"
            content_html += f'<h2 id="{section_id}">{section["title"]}</h2>\n'
            
            for paragraph in section['content']:
                content_html += f'<p>{paragraph}</p>\n'
            
            for code in section['code_examples']:
                code_html = self._format_code(code)
                content_html += f'''
                <div class="code-example">
                    <button class="copy-button"></button>
                    <pre><code>{code_html}</code></pre>
                </div>
                '''
        
        html = re.sub(
            r'<div class="blog-content">.*?</div>\s*</div>\s*</div>',
            f'<div class="blog-content">{content_html}</div></div></div>',
            html,
            flags=re.DOTALL
        )
        
        return html

    def _format_code(self, code: str) -> str:
        code = code.replace('<', '&lt;').replace('>', '&gt;')
        
        patterns = {
            'keyword': r'\b(function|class|const|let|var|return|if|else|for|while)\b',
            'function': r'\b\w+(?=\()',
            'string': r'(["\'])(.*?)\1',
            'comment': r'(//.*?$|/\*.*?\*/)'
        }
        
        for style, pattern in patterns.items():
            code = re.sub(
                pattern,
                lambda m: f'<span class="{style}">{m.group()}</span>',
                code,
                flags=re.MULTILINE
            )
        
        return code

if __name__ == "__main__":
    # Lê o conteúdo do blog de um arquivo separado
    with open('blog_content.txt', 'r', encoding='utf-8') as f:
        blog_content = f.read()

    try:
        interpreter = BlogInterpreter('template.html')
        output_file = interpreter.process_text(blog_content, 'blog')
        print(f"Blog post gerado com sucesso: {output_file}")
    except Exception as e:
        print(f"Erro ao processar o blog post: {str(e)}")