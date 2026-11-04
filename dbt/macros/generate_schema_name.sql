{% macro generate_schema_name(custom_schema_name, node) -%}
    {#- Without this macro dbt prefixes every custom schema with the profile's default schema,
        producing ``dbt_dev_staging`` instead of ``staging``. -#}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
