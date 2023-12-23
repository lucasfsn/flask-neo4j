from flask import Flask, jsonify, request
from neo4j import GraphDatabase
from dotenv import load_dotenv
import os

load_dotenv()

app = Flask(__name__)

uri = os.getenv("URI")
user = os.getenv("USERNAME")
password = os.getenv("PASSWORD")

driver = GraphDatabase.driver(uri, auth=(user, password), database="neo4j")


def get_employees(tx, filters, sort):
    query = "MATCH (e:Employee)-[r]->(d:Department)"

    if filters:
        query += " WHERE"
        for i, (filterField, filterValue) in enumerate(filters.items()):
            if i > 0:
                query += " AND"
            if filterField == "position":
                type = "MANAGES" if filterValue == "manager" else "WORKS_IN"
                query += f" TYPE(r)='{type}'"
            elif filterField == "age":
                query += f" e.{filterField}={filterValue}"
            else:
                query += f" e.{filterField}='{filterValue}'"

    query += " RETURN e.firstName as firstName, e.lastName as lastName, e.age as age"

    if sort:
        query += f" ORDER BY {sort} DESC"

    results = tx.run(query).data()
    employees = [
        {
            "firstName": result["firstName"],
            "lastName": result["lastName"],
            "age": result["age"],
        }
        for result in results
    ]
    return employees


@app.route("/employees", methods=["GET"])
def get_employees_route():
    filters = request.args.to_dict()
    sort = filters.pop("sort", None)

    with driver.session() as session:
        employees = session.read_transaction(
            get_employees,
            filters,
            sort,
        )

    if not employees:
        return (
            jsonify({"message": "No employees were found or invalid parameters"}),
            404,
        )

    response = {"employees": employees}
    return jsonify(response), 200


def add_employee(tx, firstName, lastName, age, position, department):
    if None in [firstName, lastName, age, position, department]:
        return None

    exist_department = "MATCH (d:Department {name: $department}) RETURN d"
    result_department = tx.run(exist_department, department=department)

    if result_department.single() is None:
        return None

    exist_employee = "MATCH (e:Employee {firstName: $firstName, lastName: $lastName, age: $age}) RETURN e"
    result_employee = tx.run(
        exist_employee, firstName=firstName, lastName=lastName, age=age
    )

    if result_employee.single() is not None:
        return None

    add = "CREATE (e:Employee {firstName: $firstName, lastName: $lastName, age: $age})"

    tx.run(add, firstName=firstName, lastName=lastName, age=age)

    work = "MATCH (e:Employee {firstName: $firstName, lastName: $lastName}), (d:Department {name: $department}) CREATE (e)-[:WORKS_IN]->(d)"
    tx.run(work, firstName=firstName, lastName=lastName, department=department)

    if position.lower() == "manager":
        manage = "MATCH (e:Employee {firstName: $firstName, lastName: $lastName}), (d:Department {name: $department}) CREATE (e)-[:MANAGES]->(d) RETURN e"
        tx.run(manage, firstName=firstName, lastName=lastName, department=department)

    return True


@app.route("/employees", methods=["POST"])
def add_employee_route():
    firstName = request.json.get("firstName", None)
    lastName = request.json.get("lastName", None)
    age = request.json.get("age", None)
    position = request.json.get("position", None)
    department = request.json.get("department", None)

    with driver.session() as session:
        employee = session.write_transaction(
            add_employee, firstName, lastName, age, position, department
        )

    if employee is None:
        return (
            jsonify({"message": "Employee already exists or fields are missing"}),
            500,
        )

    response = {"status": "Employee added successfully"}
    return jsonify(response), 201


def edit_employee(tx, id, firstName, lastName, age, position, department):
    params = {"id": id}

    result = tx.run("MATCH (e:Employee) WHERE id(e)=$id RETURN e", id=id).data()

    if not result:
        return {
            "status": "error",
            "message": "No employee found with the given id",
        }

    if any([firstName, lastName, age]):
        query = "MATCH (e:Employee) WHERE id(e)=$id SET"

        if firstName is not None:
            query += " e.firstName = $firstName, "
            params["firstName"] = firstName
        if lastName is not None:
            query += " e.lastName = $lastName, "
            params["lastName"] = lastName
        if age is not None:
            query += " e.age = $age, "
            params["age"] = age

        query = query.rstrip(", ")

        query += " RETURN e"

        tx.run(query, **params)

    if position is not None and department is None:
        return {
            "status": "error",
            "message": "Department cannot be None when position is provided",
        }

    if department is not None:
        exist_department = "MATCH (d:Department {name: $department}) RETURN d"
        result_department = tx.run(exist_department, department=department)

        if result_department.single() is None:
            return None

        tx.run(
            "MATCH (e:Employee)-[r:WORKS_IN|MANAGES]->() WHERE id(e)=$id DELETE r",
            id=id,
        )
        tx.run(
            "MATCH (e:Employee), (d:Department {name: $department}) WHERE id(e)=$id CREATE (e)-[:WORKS_IN]->(d)",
            id=id,
            department=department,
        )

        if position is not None and position.lower() == "manager":
            tx.run(
                "MATCH (e:Employee), (d:Department {name: $department}) WHERE id(e)=$id CREATE (e)-[:MANAGES]->(d)",
                id=id,
                department=department,
            )

    return {"status": "success", "message": "Employee edited successfully."}


@app.route("/employees/<int:id>", methods=["PUT"])
def edit_employee_route(id):
    firstName = request.json.get("firstName", None)
    lastName = request.json.get("lastName", None)
    age = request.json.get("age", None)
    position = request.json.get("position", None)
    department = request.json.get("department", None)

    with driver.session() as session:
        res = session.write_transaction(
            edit_employee, id, firstName, lastName, age, position, department
        )

    if res is None:
        return jsonify({"message": "An error occurred while editing the employee"}), 500

    if res["status"] == "error":
        return jsonify({"message": res["message"]}), 400

    response = {"status": "success", "message": res["message"]}
    return jsonify(response), 201


def delete_employee(tx, id):
    result = tx.run("MATCH (e:Employee) WHERE id(e)=$id RETURN e", id=id).data()

    if not result:
        return None

    isManager = tx.run(
        "MATCH (e:Employee)-[r:MANAGES]->(d:Department) WHERE id(e)=$id RETURN e", id=id
    )

    if not isManager.peek():
        tx.run("MATCH (e:Employee) WHERE id(e)=$id DETACH DELETE e", id=id)
        return True

    delete = "MATCH (e:Employee)-[r:MANAGES]->(d:Department) WHERE id(e)=$id DETACH DELETE e,d"
    tx.run(delete, id=id)
    return True


@app.route("/employees/<int:id>", methods=["DELETE"])
def delete_employee_route(id):
    with driver.session() as session:
        employee = session.write_transaction(delete_employee, id)

    if not employee:
        response = {"message": "Employee not found"}
        return jsonify(response), 404

    response = {"status": "Employee deleted successfully"}
    return jsonify(response), 201


def get_subordinates(tx, id):
    query = "MATCH (m:Employee)-[:MANAGES]->(d:Department)<-[:WORKS_IN]-(s:Employee) WHERE id(m)=$id AND NOT (s)-[:MANAGES]->(d) RETURN s.firstName as firstName, s.lastName as lastName, s.age as age"
    results = tx.run(query, id=id).data()
    subordinates = [
        {
            "firstName": result["firstName"],
            "lastName": result["lastName"],
            "age": result["age"],
        }
        for result in results
    ]
    return subordinates


@app.route("/employees/<int:id>/subordinates", methods=["GET"])
def get_subordinates_route(id):
    with driver.session() as session:
        employees = session.read_transaction(get_subordinates, id)

    if not employees:
        return jsonify({"message": "No subordinates were found"}), 404

    response = {"employees": employees}
    return jsonify(response), 200


def get_employee_department(tx, id):
    query = "MATCH (e:Employee)-[:WORKS_IN]->(d:Department)<-[:MANAGES]-(m:Employee) WHERE id(e)=$id WITH d, m MATCH (all:Employee)-[:WORKS_IN]->(d) RETURN d.name as department, count(all) as numberOfEmployees, m.firstName + ' ' + m.lastName as manager"

    result = tx.run(query, id=id).data()

    if result:
        return result[0]
    else:
        return None


@app.route("/employees/<int:id>/department", methods=["GET"])
def get_employee_department_route(id):
    with driver.session() as session:
        department = session.read_transaction(get_employee_department, id)

    if not department:
        return jsonify({"message": "Employee or department not found"}), 404

    response = {"department": department}
    return jsonify(response), 200


def get_departments(tx, filters, sort):
    query = "MATCH (d:Department)<-[:WORKS_IN]-(e)"

    if filters:
        query += " WHERE"
        for i, (filterField, filterValue) in enumerate(filters.items()):
            if i > 0:
                query += " AND"
            query += f" d.{filterField}='{filterValue}'"

    query += " RETURN d.name as name, count(e) as numberOfEmployees"

    if sort:
        query += f" ORDER BY {sort} DESC"

    results = tx.run(query).data()
    departments = [
        {
            "name": result["name"],
            "numberOfEmployees": result["numberOfEmployees"],
        }
        for result in results
    ]
    return departments


@app.route("/departments", methods=["GET"])
def get_departments_route():
    filters = request.args.to_dict()
    sort = filters.pop("sort", None)

    with driver.session() as session:
        departments = session.read_transaction(
            get_departments,
            filters,
            sort,
        )

    if not departments:
        return (
            jsonify({"message": "No departments were found or invalid parameters"}),
            404,
        )

    response = {"departments": departments}
    return jsonify(response), 200


def get_department_employees(tx, id):
    query = "MATCH (e:Employee)-[:WORKS_IN]->(d:Department) WHERE id(d)=$id RETURN e.firstName as firstName, e.lastName as lastName, e.age as age"

    results = tx.run(query, id=id).data()
    employees = [
        {
            "firstName": result["firstName"],
            "lastName": result["lastName"],
            "age": result["age"],
        }
        for result in results
    ]
    return employees


@app.route("/departments/<int:id>/employees", methods=["GET"])
def get_department_employees_route(id):
    with driver.session() as session:
        employees = session.read_transaction(get_department_employees, id)

    if not employees:
        return jsonify({"message": "No employees were found"}), 404

    response = {"employees": employees}
    return jsonify(response), 200


if __name__ == "__main__":
    app.run()
