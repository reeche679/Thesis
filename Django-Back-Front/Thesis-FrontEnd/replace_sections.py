# One-time script to replace static semester blocks with dynamic loop in generate_pos.html
path = "templates/pos/generate_pos.html"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

start_marker = "    <!-- FIRST YEAR - FIRST SEMESTER -->"
end_marker = "\n\n    <div class=\"mt-4 d-flex justify-content-end gap-2\">"
i = content.find(start_marker)
j = content.find(end_marker)
if i == -1 or j == -1:
    print("Markers not found", i, j)
    exit(1)
# We want to replace from start_marker up to and including the "</div>\n\n" before mt-4 div
# So we replace from i to j (exclusive of the mt-4 line)
old_block = content[i:j]
new_block = """{% for item in display_sections %}
    <div class="mb-4" id="main-sem-{{ forloop.counter0 }}" data-section-index="{{ forloop.counter0 }}">
        <h6 class="fw-bold mb-2">{{ item.section.label }}</h6>
        <button type="button" class="btn btn-sm btn-outline-primary mb-2" data-section-index="{{ forloop.counter0 }}">+ Add courses</button>
        <div class="table-responsive">
            <table class="table table-sm align-middle table-bordered mb-0">
                <thead class="table-light">
                    <tr class="text-center">
                        <th rowspan="2" style="width: 8%;">Course Code</th>
                        <th rowspan="2" style="width: 30%;">Course Title</th>
                        <th rowspan="2" style="width: 5%;">Units</th>
                        <th colspan="2" class="hours-header" style="width: 8%;">No. of hour/s</th>
                        <th rowspan="2" style="width: 13%;">Pre-requisite(s)</th>
                        <th rowspan="2" style="width: 13%;">Co-requisite(s)</th>
                        <th rowspan="2" style="width: 10%;">Grade</th>
                    </tr>
                    <tr class="text-center">
                        <th style="width: 4%;">Lec</th>
                        <th style="width: 4%;">Lab</th>
                    </tr>
                </thead>
                <tbody>
                    {% for cg in item.courses_with_grades %}
                    <tr>
                        <td class="text-center">{{ cg.course.code }}</td>
                        <td class="course-title-cell">{{ cg.course.title }}</td>
                        <td class="text-center">{{ cg.course.units }}</td>
                        <td class="text-center">{{ cg.course.lec }}</td>
                        <td class="text-center">{{ cg.course.lab }}</td>
                        <td class="small text-muted text-center">{{ cg.course.prereq|default:"" }}</td>
                        <td class="small text-muted text-center">{{ cg.course.coreq|default:"" }}</td>
                        <td>
                            <select class="form-select form-select-sm grade-select" data-course-code="{{ cg.course.code }}">
                                <option value="" {% if not cg.grade %}selected{% endif %}></option>
                                <option value="1.00" {% if cg.grade == '1.00' %}selected{% endif %}>1.00</option>
                                <option value="1.25" {% if cg.grade == '1.25' %}selected{% endif %}>1.25</option>
                                <option value="1.50" {% if cg.grade == '1.50' %}selected{% endif %}>1.50</option>
                                <option value="1.75" {% if cg.grade == '1.75' %}selected{% endif %}>1.75</option>
                                <option value="2.00" {% if cg.grade == '2.00' %}selected{% endif %}>2.00</option>
                                <option value="2.25" {% if cg.grade == '2.25' %}selected{% endif %}>2.25</option>
                                <option value="2.50" {% if cg.grade == '2.50' %}selected{% endif %}>2.50</option>
                                <option value="2.75" {% if cg.grade == '2.75' %}selected{% endif %}>2.75</option>
                                <option value="3.00" {% if cg.grade == '3.00' %}selected{% endif %}>3.00</option>
                                <option value="5.00" {% if cg.grade == '5.00' %}selected{% endif %}>5.00</option>
                            </select>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
{% endfor %}

"""
new_content = content[:i] + new_block + content[j:]
with open(path, "w", encoding="utf-8") as f:
    f.write(new_content)
print("Replaced successfully")
